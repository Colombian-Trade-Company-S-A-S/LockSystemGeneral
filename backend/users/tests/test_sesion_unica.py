"""Sesión única: una cuenta en un solo navegador, un navegador con una sola cuenta.

Todo va contra la API real mandando la cabecera `X-Device-Id`, que es como se
identifica cada navegador. Nada de `force_authenticate`: eso se salta la
autenticación JWT, que es justo donde vive la mitad de la regla.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()

STRONG_PASSWORD = 'ClaveSegura123'
PLAZO = 10

# Dos "navegadores" distintos: el del trabajo y el de casa.
CHROME_TRABAJO = 'chrome-trabajo-0001'
FIREFOX_CASA = 'firefox-casa-0002'


@override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
class SesionUnicaTests(APITestCase):
    def setUp(self):
        cache.clear()  # aísla el throttle de login (5/min) entre tests
        self.ana = User.objects.create_user(
            email='ana@correo.com', password=STRONG_PASSWORD
        )
        self.beto = User.objects.create_user(
            email='beto@correo.com', password=STRONG_PASSWORD
        )

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def entrar(self, email, dispositivo):
        return self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': email, 'password': STRONG_PASSWORD},
            format='json',
            HTTP_X_DEVICE_ID=dispositivo,
        )

    def pedir_perfil(self, access, dispositivo=None):
        cabeceras = {'HTTP_AUTHORIZATION': f'Bearer {access}'}
        if dispositivo is not None:
            cabeceras['HTTP_X_DEVICE_ID'] = dispositivo
        return self.client.get(reverse('users:me'), **cabeceras)

    def abandonar(self, user):
        """Simula que la sesión de `user` caducó por inactividad."""
        User.objects.filter(pk=user.pk).update(
            last_activity=timezone.now() - timedelta(minutes=PLAZO + 1)
        )

    # ------------------------------------------------------------------
    # Regla 1: una cuenta, un solo navegador
    # ------------------------------------------------------------------
    def test_la_misma_cuenta_no_entra_desde_otro_navegador(self):
        self.assertEqual(
            self.entrar('ana@correo.com', CHROME_TRABAJO).status_code, 200
        )

        resp = self.entrar('ana@correo.com', FIREFOX_CASA)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['detail'].code, 'sesion_en_otro_dispositivo')

    def test_volver_a_entrar_desde_el_mismo_navegador_si_se_permite(self):
        """Recargar la página o cerrar la pestaña y volver no puede dejarte fuera."""
        self.assertEqual(
            self.entrar('ana@correo.com', CHROME_TRABAJO).status_code, 200
        )
        self.assertEqual(
            self.entrar('ana@correo.com', CHROME_TRABAJO).status_code, 200
        )

    def test_el_mensaje_dice_donde_quedo_abierta(self):
        self.entrar('ana@correo.com', CHROME_TRABAJO)
        resp = self.entrar('ana@correo.com', FIREFOX_CASA)
        self.assertIn('sesión abierta en', str(resp.data['detail']))

    # ------------------------------------------------------------------
    # Regla 2: un navegador, una sola cuenta
    # ------------------------------------------------------------------
    def test_otra_cuenta_no_entra_en_el_navegador_ocupado(self):
        self.assertEqual(
            self.entrar('ana@correo.com', CHROME_TRABAJO).status_code, 200
        )

        resp = self.entrar('beto@correo.com', CHROME_TRABAJO)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['detail'].code, 'dispositivo_ocupado')

    def test_otra_cuenta_si_entra_desde_otro_navegador(self):
        self.entrar('ana@correo.com', CHROME_TRABAJO)
        self.assertEqual(
            self.entrar('beto@correo.com', FIREFOX_CASA).status_code, 200
        )

    # ------------------------------------------------------------------
    # El token queda atado al navegador que abrió la sesión
    # ------------------------------------------------------------------
    def test_token_llevado_a_otro_navegador_no_sirve(self):
        """Un token robado no vale sin el navegador desde el que se emitió."""
        access = self.entrar('ana@correo.com', CHROME_TRABAJO).data['access']

        self.assertEqual(self.pedir_perfil(access, CHROME_TRABAJO).status_code, 200)
        self.assertEqual(self.pedir_perfil(access, FIREFOX_CASA).status_code, 401)

    def test_token_sin_identificar_el_navegador_no_sirve(self):
        access = self.entrar('ana@correo.com', CHROME_TRABAJO).data['access']
        self.assertEqual(self.pedir_perfil(access).status_code, 401)

    def test_refresh_desde_otro_navegador_no_sirve(self):
        """Renovar el token sería la forma de esquivar la regla si no se mirara."""
        refresh = self.entrar('ana@correo.com', CHROME_TRABAJO).data['refresh']

        resp = self.client.post(
            reverse('users:token_refresh'),
            {'refresh': refresh},
            format='json',
            HTTP_X_DEVICE_ID=FIREFOX_CASA,
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    # ------------------------------------------------------------------
    # Cómo se libera la cuenta / el navegador
    # ------------------------------------------------------------------
    def test_tras_cerrar_sesion_la_cuenta_entra_desde_otro_navegador(self):
        access = self.entrar('ana@correo.com', CHROME_TRABAJO).data['access']
        self.client.post(
            reverse('users:logout'),
            HTTP_AUTHORIZATION=f'Bearer {access}',
            HTTP_X_DEVICE_ID=CHROME_TRABAJO,
        )
        self.assertEqual(
            self.entrar('ana@correo.com', FIREFOX_CASA).status_code, 200
        )

    def test_tras_cerrar_sesion_el_navegador_acepta_otra_cuenta(self):
        """Un PC compartido tiene que poder pasar de un turno al siguiente."""
        access = self.entrar('ana@correo.com', CHROME_TRABAJO).data['access']
        self.client.post(
            reverse('users:logout'),
            HTTP_AUTHORIZATION=f'Bearer {access}',
            HTTP_X_DEVICE_ID=CHROME_TRABAJO,
        )
        self.assertEqual(
            self.entrar('beto@correo.com', CHROME_TRABAJO).status_code, 200
        )

    def test_la_sesion_abandonada_deja_de_ocupar_la_cuenta(self):
        """Cerrar el navegador sin desconectarse no puede dejarte fuera para siempre."""
        self.entrar('ana@correo.com', CHROME_TRABAJO)
        self.abandonar(self.ana)

        self.assertEqual(
            self.entrar('ana@correo.com', FIREFOX_CASA).status_code, 200
        )

    def test_la_sesion_abandonada_deja_libre_el_navegador(self):
        self.entrar('ana@correo.com', CHROME_TRABAJO)
        self.abandonar(self.ana)

        self.assertEqual(
            self.entrar('beto@correo.com', CHROME_TRABAJO).status_code, 200
        )
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.device_id, '')

    @override_settings(SESSION_IDLE_TIMEOUT_MINUTES=0)
    def test_sin_cierre_por_inactividad_la_regla_sigue_en_pie(self):
        """La sesión única no depende de que el timeout esté activado."""
        self.assertEqual(
            self.entrar('ana@correo.com', CHROME_TRABAJO).status_code, 200
        )
        self.assertEqual(
            self.entrar('ana@correo.com', FIREFOX_CASA).status_code,
            status.HTTP_409_CONFLICT,
        )


@override_settings(SESSION_IDLE_TIMEOUT_MINUTES=PLAZO)
class CierreForzadoPorAdminTests(APITestCase):
    """La válvula de escape: un administrador libera una cuenta atascada."""

    def setUp(self):
        cache.clear()
        from empresas.models import Empresa

        self.empresa = Empresa.objects.create(nombre='Empresa Uno')
        self.otra_empresa = Empresa.objects.create(nombre='Empresa Dos')
        self.admin = User.objects.create_user(
            email='admin@correo.com',
            password=STRONG_PASSWORD,
            role='admin',
            empresa=self.empresa,
        )
        self.ana = User.objects.create_user(
            email='ana@correo.com', password=STRONG_PASSWORD, empresa=self.empresa
        )
        self.url = reverse('users:usuario-cerrar-sesion', args=[self.ana.pk])

    def _ana_con_sesion(self):
        resp = self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': 'ana@correo.com', 'password': STRONG_PASSWORD},
            format='json',
            HTTP_X_DEVICE_ID=CHROME_TRABAJO,
        )
        self.assertEqual(resp.status_code, 200)

    def test_admin_libera_la_cuenta(self):
        self._ana_con_sesion()
        self.client.force_authenticate(user=self.admin)

        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['sesion_activa'])

        # Y ahora sí puede entrar desde el otro navegador.
        self.client.force_authenticate(user=None)
        entrada = self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': 'ana@correo.com', 'password': STRONG_PASSWORD},
            format='json',
            HTTP_X_DEVICE_ID=FIREFOX_CASA,
        )
        self.assertEqual(entrada.status_code, 200)

    def test_un_usuario_normal_no_puede_cerrar_sesiones_ajenas(self):
        self.client.force_authenticate(user=self.ana)
        self.assertEqual(
            self.client.post(self.url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_anonimo_no_puede(self):
        self.assertEqual(
            self.client.post(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_admin_de_otra_empresa_no_ve_la_cuenta(self):
        """Acotado por empresa: da 404, que no confirma ni que el id exista."""
        ajeno = User.objects.create_user(
            email='ajeno@correo.com',
            password=STRONG_PASSWORD,
            role='admin',
            empresa=self.otra_empresa,
        )
        self.client.force_authenticate(user=ajeno)
        self.assertEqual(
            self.client.post(self.url).status_code, status.HTTP_404_NOT_FOUND
        )
