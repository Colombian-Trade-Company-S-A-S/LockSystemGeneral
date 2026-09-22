"""Cierre de sesión por inactividad (SESSION_IDLE_TIMEOUT_MINUTES).

Lo que se comprueba es que el corte lo impone el SERVIDOR: no basta con que el
navegador borre sus tokens, un token guardado tiene que dejar de servir. Por eso
los tests van contra la API real con el header Authorization, y no contra
`force_authenticate` (que se salta la autenticación JWT).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.session import DEVICE_HEADER, registrar_actividad, sesion_expirada

User = get_user_model()

STRONG_PASSWORD = 'ClaveSegura123'

# Plazo usado en los tests: 10 minutos. Se pone explícito con override_settings
# para no depender del valor que tenga el `.env` de quien ejecute la suite.
PLAZO = 10


class SesionExpiradaTests(APITestCase):
    """Reglas de `sesion_expirada` (la decisión pura, sin HTTP de por medio)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='user@correo.com', password=STRONG_PASSWORD
        )

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
    def test_dentro_del_plazo_sigue_viva(self):
        self.user.last_activity = timezone.now() - timedelta(minutes=PLAZO - 1)
        self.assertFalse(sesion_expirada(self.user))

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
    def test_pasado_el_plazo_expira(self):
        self.user.last_activity = timezone.now() - timedelta(minutes=PLAZO + 1)
        self.assertTrue(sesion_expirada(self.user))

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
    def test_sin_marca_previa_no_se_corta(self):
        """Una cuenta que nunca registró actividad no se echa por sorpresa."""
        self.user.last_activity = None
        self.assertFalse(sesion_expirada(self.user))

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=0)
    def test_en_cero_no_hay_cierre_automatico(self):
        self.user.last_activity = timezone.now() - timedelta(days=30)
        self.assertFalse(sesion_expirada(self.user))

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
    def test_registrar_actividad_persiste_la_marca(self):
        registrar_actividad(self.user, forzar=True)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_activity)

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
    def test_registrar_actividad_no_toca_updated_at(self):
        """`updated_at` audita cambios del perfil, no el uso de la aplicación."""
        antes = User.objects.get(pk=self.user.pk).updated_at
        registrar_actividad(self.user, forzar=True)
        self.assertEqual(User.objects.get(pk=self.user.pk).updated_at, antes)


@override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
class SesionInactivaApiTests(APITestCase):
    """El servidor rechaza los tokens de una sesión abandonada."""

    def setUp(self):
        cache.clear()  # aísla el throttle de login (5/min) entre tests
        self.user = User.objects.create_user(
            email='user@correo.com', password=STRONG_PASSWORD
        )
        resp = self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': 'user@correo.com', 'password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.access = resp.data['access']
        self.refresh = resp.data['refresh']
        # Un cliente real guarda el identificador de navegador que le devuelve
        # el login y lo reenvía en cada petición (ver sesión única).
        self.device = resp.data['device_id']

    def _envejecer_sesion(self, minutos):
        """Simula que el usuario lleva `minutos` sin tocar nada."""
        User.objects.filter(pk=self.user.pk).update(
            last_activity=timezone.now() - timedelta(minutes=minutos)
        )

    def _pedir_perfil(self):
        return self.client.get(
            reverse('users:me'),
            HTTP_AUTHORIZATION=f'Bearer {self.access}',
            **{f'HTTP_{DEVICE_HEADER.upper().replace("-", "_")}': self.device},
        )

    def _refrescar(self):
        return self.client.post(
            reverse('users:token_refresh'),
            {'refresh': self.refresh},
            format='json',
            **{f'HTTP_{DEVICE_HEADER.upper().replace("-", "_")}': self.device},
        )

    def test_login_arranca_el_reloj(self):
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_activity)

    def test_usuario_activo_entra(self):
        self.assertEqual(self._pedir_perfil().status_code, status.HTTP_200_OK)

    def test_cada_peticion_renueva_el_plazo(self):
        self._envejecer_sesion(PLAZO - 1)  # dentro del plazo: pasa y renueva
        self.assertEqual(self._pedir_perfil().status_code, status.HTTP_200_OK)

        # Tras esa petición el reloj vuelve a cero, así que otro rato igual de
        # largo tampoco la cierra: es inactividad, no duración total.
        self._envejecer_sesion(PLAZO - 1)
        self.assertEqual(self._pedir_perfil().status_code, status.HTTP_200_OK)

    def test_token_de_sesion_abandonada_no_sirve(self):
        self._envejecer_sesion(PLAZO + 1)
        resp = self._pedir_perfil()
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_no_resucita_una_sesion_vencida(self):
        """El agujero clásico: renovar el token para revivir una sesión muerta."""
        self._envejecer_sesion(PLAZO + 1)
        self.assertEqual(self._refrescar().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_dentro_del_plazo_sigue_funcionando(self):
        resp = self._refrescar()
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=0)
    def test_desactivado_no_cierra_nada(self):
        self._envejecer_sesion(60 * 24)  # un día entero sin tocar la app
        self.assertEqual(self._pedir_perfil().status_code, status.HTTP_200_OK)

    def test_el_perfil_publica_el_plazo_para_el_frontend(self):
        resp = self._pedir_perfil()
        self.assertEqual(resp.data['session_timeout_minutes'], PLAZO)
