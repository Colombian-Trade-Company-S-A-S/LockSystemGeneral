"""Vistas HTTP de la app `users` (capa delgada: HTTP ↔ dominio)."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import filters, generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from empresas.models import Empresa
from empresas.scoping import acotar, es_acceso_global
from users.authentication import (
    TOKEN_VERSION_CLAIM,
    empresa_inactiva,
    token_esta_revocado,
)
from users.permissions import IsAdminRole
from users.session import (
    abrir_sesion,
    describir_dispositivo,
    dispositivo_ajeno,
    dispositivo_de,
    registrar_actividad,
    sesion_expirada,
    verificar_puede_iniciar_sesion,
)
from users.selectors import user_list
from users.services import user_set_password

from .serializers import (
    AdminUserCreateSerializer,
    AdminUserUpdateSerializer,
    ChangePasswordSerializer,
    MeSerializer,
    MeUpdateSerializer,
    UserSerializer,
)

User = get_user_model()

# NOTA: no existe una vista de auto-registro público. Las cuentas las crea un
# Administrador (`AdminUserListCreateView`, POST /api/usuarios/). Exponer un alta
# pública daría a desconocidos acceso de lectura a dispositivos y PINs.


class MeView(generics.RetrieveUpdateAPIView):
    """Perfil del usuario autenticado: consulta (GET) y edición propia (PATCH).

    Solo permite editar datos personales (nombre/apellido); el rol y los
    permisos NO son auto-editables (anti escalado de privilegios).
    """

    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.request.method == 'PATCH':
            return MeUpdateSerializer
        return MeSerializer

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        # Responde siempre con la representación segura y completa del perfil.
        return Response(MeSerializer(self.get_object()).data)


class VersionedTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Incrusta la versión de token vigente del usuario en el JWT (claim `tv`).

    Permite la revocación server-side: ver users/authentication.py.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token[TOKEN_VERSION_CLAIM] = user.token_version
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        # Empresa desactivada -> su gente no entra, aunque la cuenta siga activa.
        if empresa_inactiva(self.user):
            raise InvalidToken('La empresa de tu cuenta está desactivada.')

        # Sesión única: una cuenta en un solo navegador y un navegador con una
        # sola cuenta. Se comprueba con las credenciales ya validadas, para no
        # contarle a un desconocido quién tiene sesión abierta.
        peticion = self.context['request']
        dispositivo = dispositivo_de(peticion)
        verificar_puede_iniciar_sesion(self.user, dispositivo)
        abrir_sesion(self.user, dispositivo, describir_dispositivo(peticion))

        # El cliente guarda este identificador y lo reenvía en cada petición;
        # es lo que ata la sesión a este navegador.
        data['device_id'] = dispositivo
        return data


class LoginView(TokenObtainPairView):
    """Obtiene el par de tokens (access/refresh) con email + password."""

    serializer_class = VersionedTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'


class RevocationAwareTokenRefreshSerializer(TokenRefreshSerializer):
    """Refresh que respeta la revocación server-side (logout real).

    Rechaza un refresh emitido antes del corte del usuario, o de una cuenta
    inactiva/eliminada, antes de emitir un nuevo par de tokens.

    También comprueba la inactividad: sin esto, un refresh guardado permitiría
    resucitar una sesión ya vencida, que es justo lo que el plazo evita.
    """

    def validate(self, attrs):
        try:
            refresh = RefreshToken(attrs['refresh'])
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc

        user = User.objects.filter(id=refresh.get('user_id')).first()
        if user is None or not user.is_active:
            raise InvalidToken('La cuenta no está disponible.')
        if token_esta_revocado(user, refresh):
            raise InvalidToken('La sesión fue cerrada. Inicia sesión de nuevo.')
        if empresa_inactiva(user):
            raise InvalidToken('La empresa de tu cuenta está desactivada.')
        if sesion_expirada(user):
            raise InvalidToken('Tu sesión se cerró por inactividad.')
        # Renovar desde otro navegador sería la manera de esquivar la sesión
        # única: el refresh queda atado al dispositivo igual que el access.
        if dispositivo_ajeno(user, dispositivo_de(self.context['request'])):
            raise InvalidToken(
                'Tu sesión está abierta en otro dispositivo. Inicia sesión de nuevo.'
            )

        data = super().validate(attrs)
        # Renovar el token es señal de que el cliente sigue trabajando.
        registrar_actividad(user, forzar=True)
        return data


class RefreshView(TokenRefreshView):
    """Renueva el access token a partir de un refresh válido (y no revocado)."""

    serializer_class = RevocationAwareTokenRefreshSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'


class LogoutView(APIView):
    """Cierre de sesión real: revoca los tokens del usuario en el servidor.

    A partir de aquí, cualquier access/refresh emitido antes deja de ser válido
    (incluye los que hubieran podido filtrarse). El cliente además descarta los
    suyos localmente.

    Libera también el navegador que tenía ocupado, que es lo que permite volver
    a entrar en el acto desde otro equipo o dejarle el equipo a un compañero.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        request.user.cerrar_sesion()
        return Response(status=status.HTTP_205_RESET_CONTENT)


class ChangePasswordView(APIView):
    """Cambio de la propia contraseña.

    Al cambiarla se **revocan** las demás sesiones (bump de versión de token) y
    se **re-emite** un par de tokens para la sesión actual, para no cerrarla.
    Throttle 'login' para limitar intentos contra la contraseña actual.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)

        user = request.user
        user_set_password(
            user=user, raw_password=serializer.validated_data['new_password']
        )
        # Invalida cualquier token anterior (otras sesiones / posibles filtrados).
        user.revoke_tokens()
        # Re-emite para el cliente actual con la nueva versión de token.
        refresh = VersionedTokenObtainPairSerializer.get_token(user)
        return Response({'access': str(refresh.access_token), 'refresh': str(refresh)})


# ---------------------------------------------------------------------------
# Gestión de usuarios (solo Administrador)
# ---------------------------------------------------------------------------
def _empresa_del_nuevo_usuario(request):
    """A qué empresa pertenece el usuario que se está creando.

    Un administrador de empresa solo puede crear gente **en la suya**: la empresa
    se toma de su cuenta y se ignora lo que venga en el cuerpo. El administrador
    general no tiene empresa propia, así que tiene que decir cuál.
    """
    if not es_acceso_global(request.user):
        return request.user.empresa

    empresa_id = request.data.get('empresa')
    if not empresa_id:
        raise ValidationError(
            {'empresa': 'Indica a qué empresa pertenece el usuario.'}
        )
    empresa = Empresa.objects.filter(pk=empresa_id).first()
    if empresa is None:
        raise ValidationError({'empresa': 'La empresa indicada no existe.'})
    return empresa


class AdminUserListCreateView(generics.ListCreateAPIView):
    """Lista y crea usuarios **de la propia empresa**. Reservado a administradores.

    El administrador general (superusuario) ve los de todas y elige empresa al
    crear.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdminRole]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['email', 'first_name', 'last_name']
    ordering_fields = ['email', 'date_joined', 'role']

    def get_queryset(self):
        return acotar(user_list(), self.request.user).order_by('-date_joined')

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AdminUserCreateSerializer
        return UserSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save(empresa=_empresa_del_nuevo_usuario(request))
        # Respondemos con la vista segura (sin contraseña, con rol legible).
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class AdminUserDetailView(generics.RetrieveUpdateAPIView):
    """Consulta y edita un usuario (rol, nombre, activo). Solo administradores.

    El queryset va acotado a la empresa: pedir un usuario de otra empresa da 404
    (no 403, que confirmaría que ese id existe).
    """

    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get_queryset(self):
        return acotar(user_list(), self.request.user)

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return AdminUserUpdateSerializer
        return UserSerializer

    def perform_update(self, serializer):
        target = self.get_object()
        data = serializer.validated_data

        # Cambiar a alguien de empresa es darle acceso a los datos de otra: solo
        # el administrador general puede. Un admin de empresa que lo intente se
        # lleva un rechazo explícito (no un ignorado silencioso).
        empresa = data.get('empresa')
        if (
            empresa is not None
            and empresa != target.empresa
            and not es_acceso_global(self.request.user)
        ):
            raise PermissionDenied(
                'Solo el administrador general puede cambiar la empresa de un usuario.'
            )

        # Evita que un administrador se bloquee a sí mismo (perder acceso admin
        # o desactivar su propia cuenta) desde esta pantalla.
        if target == self.request.user:
            if data.get('role', target.role) != target.role and not target.is_superuser:
                raise ValidationError('No puedes cambiar tu propio rol.')
            if data.get('is_active', target.is_active) is False:
                raise ValidationError('No puedes desactivar tu propia cuenta.')
        serializer.save()

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        # Devuelve siempre la representación segura y completa del usuario.
        return Response(UserSerializer(self.get_object()).data)


class AdminCerrarSesionView(APIView):
    """Fuerza el cierre de la sesión de un usuario. Solo administradores.

    Es la válvula de escape de la sesión única: si alguien deja la sesión colgada
    (portátil averiado, se fue de viaje, cerró el navegador de golpe) su cuenta
    queda ocupada hasta que caduque por inactividad. Con esto un administrador la
    libera en el acto, sin esperar al reloj.

    Va acotado a la empresa igual que el resto de la gestión de usuarios: pedir
    a alguien de otra empresa da 404.
    """

    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        usuario = generics.get_object_or_404(
            acotar(user_list(), request.user), pk=pk
        )
        usuario.cerrar_sesion()
        return Response(UserSerializer(usuario).data)
