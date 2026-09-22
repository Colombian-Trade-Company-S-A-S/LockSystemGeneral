"""Serializers de la app `users`.

Principios de seguridad aplicados:
- `password` es *write-only*: nunca se serializa de vuelta.
- Solo se exponen/aceptan campos seguros. `is_staff`, `is_superuser`, `groups`
  y `user_permissions` NO son asignables desde la API (anti mass-assignment /
  escalado de privilegios).
- La fortaleza de la contraseña se valida con las reglas de Django.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from empresas.api.serializers import EmpresaBreveSerializer
from empresas.models import Empresa
from users.models import Role
from users.services import user_create, user_update
from users.session import sesion_activa

User = get_user_model()


class MeUpdateSerializer(serializers.ModelSerializer):
    """Edición del propio perfil: datos personales y preferencias (sin rol/permisos)."""

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'accent')

    def update(self, instance: User, validated_data: dict) -> User:
        return user_update(user=instance, data=validated_data)


class ChangePasswordSerializer(serializers.Serializer):
    """Cambio de la propia contraseña: exige la actual y valida la nueva."""

    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(
        write_only=True, trim_whitespace=False, max_length=128,
    )

    def validate_current_password(self, value: str) -> str:
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('La contraseña actual no es correcta.')
        return value

    def validate_new_password(self, value: str) -> str:
        user = self.context['request'].user
        validate_password(value, user=user)
        return value


class UserSerializer(serializers.ModelSerializer):
    """Representación pública/segura de un usuario (solo lectura)."""

    full_name = serializers.CharField(read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    # La empresa se expone (el front muestra a quién pertenece la sesión) pero no
    # es editable desde la API: se fija al crear la cuenta.
    empresa = EmpresaBreveSerializer(read_only=True)
    is_superadmin = serializers.BooleanField(read_only=True)
    # Estado de la sesión única: la pantalla de usuarios muestra quién tiene una
    # sesión ocupando su cuenta y ofrece cerrarla.
    sesion_activa = serializers.SerializerMethodField()
    sesion_dispositivo = serializers.CharField(
        source='device_label', read_only=True
    )

    class Meta:
        model = User
        fields = (
            'id',
            'email',
            'first_name',
            'last_name',
            'full_name',
            'role',
            'role_display',
            'empresa',
            'is_superadmin',
            'is_active',
            'date_joined',
            'accent',
            'sesion_activa',
            'sesion_dispositivo',
            'session_started_at',
        )
        read_only_fields = fields

    def get_sesion_activa(self, obj) -> bool:
        """True si la cuenta tiene una sesión abierta ocupando un navegador."""
        return sesion_activa(obj)


class MeSerializer(UserSerializer):
    """Perfil propio (/api/me/): el usuario más los ajustes de su sesión.

    Añade el plazo de inactividad para que el frontend lo lea del servidor en
    vez de llevarlo compilado: se cambia SESSION_IDLE_TIMEOUT_MINUTES en el
    `.env` del backend y la interfaz obedece sin recompilar nada. Quien manda
    sigue siendo el backend —él rechaza los tokens vencidos—; el frontend solo
    usa el dato para llevar al usuario al login en el momento justo.
    """

    session_timeout_minutes = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ('session_timeout_minutes',)
        read_only_fields = fields

    def get_session_timeout_minutes(self, obj) -> int:
        """Minutos de inactividad permitidos. 0 = sin cierre automático."""
        return settings.SESSION_IDLE_TIMEOUT_MINUTES


class AdminUserCreateSerializer(serializers.ModelSerializer):
    """Alta de usuario por un administrador (asigna rol)."""

    password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        trim_whitespace=False,
        max_length=128,
    )
    role = serializers.ChoiceField(choices=Role.choices, default=Role.CONSULTA)

    class Meta:
        model = User
        fields = ('id', 'email', 'password', 'first_name', 'last_name', 'role')
        read_only_fields = ('id',)
        extra_kwargs = {
            'first_name': {'required': False},
            'last_name': {'required': False},
        }

    def validate_email(self, value: str) -> str:
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Ya existe una cuenta con este correo.')
        return value

    def validate(self, attrs: dict) -> dict:
        temp_user = User(
            email=attrs.get('email', ''),
            first_name=attrs.get('first_name', ''),
            last_name=attrs.get('last_name', ''),
        )
        validate_password(attrs['password'], user=temp_user)
        return attrs

    def create(self, validated_data: dict) -> User:
        return user_create(**validated_data)


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    """Edición de usuario por un administrador (nombre, rol, activo, empresa).

    `empresa` solo la puede cambiar el administrador general: mover a alguien de
    empresa es darle acceso a los datos de otra, así que la vista rechaza el
    campo si lo manda cualquier otro (ver AdminUserDetailView.perform_update).
    No se acepta vaciarla: un usuario sin empresa que no sea superusuario no
    vería nada.
    """

    role = serializers.ChoiceField(choices=Role.choices, required=False)
    empresa = serializers.PrimaryKeyRelatedField(
        queryset=Empresa.objects.all(), required=False, allow_null=False,
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'role', 'is_active', 'empresa')

    def update(self, instance: User, validated_data: dict) -> User:
        return user_update(user=instance, data=validated_data)
