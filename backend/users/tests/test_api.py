from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from empresas.models import Empresa

User = get_user_model()

STRONG_PASSWORD = 'ClaveSegura123'


class NoPublicRegisterTests(APITestCase):
    """El auto-registro público quedó deshabilitado (endpoint eliminado)."""

    def test_ruta_de_registro_no_existe(self):
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse('users:register')


class AdminUserCreateApiTests(APITestCase):
    """Alta de usuarios: reservada a administradores (POST /api/usuarios/)."""

    def setUp(self):
        self.url = reverse('users:usuarios')
        # El admin es administrador DE UNA EMPRESA (el caso normal): la empresa
        # del usuario que crea es la que hereda el nuevo, sin elegirla.
        self.empresa = Empresa.objects.create(nombre='Empresa Uno')
        self.admin = User.objects.create_user(
            email='admin@correo.com',
            password=STRONG_PASSWORD,
            role='admin',
            empresa=self.empresa,
        )
        self.consulta = User.objects.create_user(
            email='consulta@correo.com',
            password=STRONG_PASSWORD,
            empresa=self.empresa,
        )

    def test_anonimo_no_puede_crear(self):
        resp = self.client.post(
            self.url,
            {'email': 'x@correo.com', 'password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(User.objects.filter(email='x@correo.com').exists())

    def test_no_admin_no_puede_crear(self):
        self.client.force_authenticate(user=self.consulta)
        resp = self.client.post(
            self.url,
            {'email': 'x@correo.com', 'password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(email='x@correo.com').exists())

    def test_admin_crea_usuario_sin_devolver_password(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.url,
            {'email': 'nuevo@correo.com', 'password': STRONG_PASSWORD, 'role': 'operador'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('password', resp.data)
        self.assertEqual(resp.data['email'], 'nuevo@correo.com')
        self.assertEqual(resp.data['role'], 'operador')

    def test_password_debil_rechazada(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.url,
            {'email': 'debil@correo.com', 'password': '123'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email='debil@correo.com').exists())

    def test_no_permite_escalar_privilegios(self):
        # Aunque envíe is_staff/is_superuser, no son asignables desde la API.
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self.url,
            {
                'email': 'attacker@correo.com',
                'password': STRONG_PASSWORD,
                'is_staff': True,
                'is_superuser': True,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email='attacker@correo.com')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_email_duplicado_rechazado(self):
        self.client.force_authenticate(user=self.admin)
        User.objects.create_user(email='dup@correo.com', password=STRONG_PASSWORD)
        resp = self.client.post(
            self.url,
            {'email': 'DUP@correo.com', 'password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class TokenApiTests(APITestCase):
    def setUp(self):
        cache.clear()  # aísla el throttle de login (5/min) entre tests
        self.user = User.objects.create_user(
            email='login@correo.com', password=STRONG_PASSWORD
        )

    def test_obtiene_token_con_email(self):
        resp = self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': 'login@correo.com', 'password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)

    def test_credenciales_invalidas(self):
        resp = self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': 'login@correo.com', 'password': 'incorrecta'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutRevocationApiTests(APITestCase):
    """El logout revoca server-side los tokens ya emitidos (versión de token)."""

    def setUp(self):
        cache.clear()  # aísla el throttle de login (5/min) entre tests
        self.user = User.objects.create_user(
            email='revoke@correo.com', password=STRONG_PASSWORD
        )
        resp = self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': 'revoke@correo.com', 'password': STRONG_PASSWORD},
            format='json',
        )
        self.access = resp.data['access']
        self.refresh = resp.data['refresh']
        # Sesión única: el cliente reenvía el navegador que le asignó el login.
        self.device = resp.data['device_id']

    def _auth(self, token):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_X_DEVICE_ID=self.device
        )

    def test_logout_invalida_access_y_refresh(self):
        self._auth(self.access)
        self.assertEqual(self.client.get(reverse('users:me')).status_code, 200)

        # Cierra sesión: revoca en el servidor.
        self.assertEqual(
            self.client.post(reverse('users:logout')).status_code,
            status.HTTP_205_RESET_CONTENT,
        )

        # El access anterior ya no sirve.
        self.assertEqual(self.client.get(reverse('users:me')).status_code, 401)

        # El refresh anterior ya no emite tokens.
        self.client.credentials()
        r = self.client.post(
            reverse('users:token_refresh'),
            {'refresh': self.refresh},
            format='json',
            HTTP_X_DEVICE_ID=self.device,
        )
        self.assertEqual(r.status_code, 401)

    def test_reinicio_de_sesion_funciona_tras_logout(self):
        self._auth(self.access)
        self.client.post(reverse('users:logout'))
        self.client.credentials()

        # Un login nuevo debe funcionar de inmediato (sin ambigüedad temporal).
        r = self.client.post(
            reverse('users:token_obtain_pair'),
            {'email': 'revoke@correo.com', 'password': STRONG_PASSWORD},
            format='json',
        )
        self.assertEqual(r.status_code, 200)
        self.device = r.data['device_id']
        self._auth(r.data['access'])
        self.assertEqual(self.client.get(reverse('users:me')).status_code, 200)


class MeUpdateApiTests(APITestCase):
    """Edición del propio perfil (PATCH /api/me/)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='self@correo.com', password=STRONG_PASSWORD
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse('users:me')

    def test_edita_su_nombre(self):
        resp = self.client.patch(
            self.url, {'first_name': 'Ada', 'last_name': 'Lovelace'}, format='json'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['full_name'], 'Ada Lovelace')

    def test_no_puede_auto_escalar_rol_ni_staff(self):
        self.client.patch(
            self.url, {'role': 'admin', 'is_staff': True}, format='json'
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'consulta')
        self.assertFalse(self.user.is_staff)


class ChangePasswordApiTests(APITestCase):
    """Cambio de la propia contraseña (POST /api/auth/password/)."""

    NEW_PASSWORD = 'NuevaClave-2026'

    def setUp(self):
        cache.clear()  # aísla el throttle 'login' entre tests
        self.user = User.objects.create_user(
            email='pass@correo.com', password=STRONG_PASSWORD
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse('users:change_password')

    def test_contrasena_actual_incorrecta(self):
        resp = self.client.post(
            self.url,
            {'current_password': 'incorrecta', 'new_password': self.NEW_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_contrasena_nueva_debil_rechazada(self):
        resp = self.client.post(
            self.url,
            {'current_password': STRONG_PASSWORD, 'new_password': '123'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cambio_exitoso_revoca_y_reemite(self):
        version_previa = self.user.token_version
        resp = self.client.post(
            self.url,
            {'current_password': STRONG_PASSWORD, 'new_password': self.NEW_PASSWORD},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW_PASSWORD))
        self.assertGreater(self.user.token_version, version_previa)


class MeApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='me@correo.com', password=STRONG_PASSWORD, first_name='Ada'
        )
        self.url = reverse('users:me')

    def test_me_requiere_autenticacion(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_devuelve_perfil_autenticado(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['email'], 'me@correo.com')
        self.assertEqual(resp.data['full_name'], 'Ada')
        self.assertNotIn('password', resp.data)
