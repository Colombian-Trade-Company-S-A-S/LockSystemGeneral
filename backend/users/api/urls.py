from django.urls import path
from rest_framework_simplejwt.views import TokenVerifyView

from .views import (
    AdminCerrarSesionView,
    AdminUserDetailView,
    AdminUserListCreateView,
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
)

app_name = 'users'

urlpatterns = [
    # Autenticación.
    # NOTA: el auto-registro público está deshabilitado a propósito (era un
    # vector: cualquiera podía crear una cuenta con acceso de lectura a
    # dispositivos y PINs). Las cuentas las crea un Administrador en /usuarios.
    path('auth/token/', LoginView.as_view(), name='token_obtain_pair'),
    path('auth/token/refresh/', RefreshView.as_view(), name='token_refresh'),
    path('auth/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/password/', ChangePasswordView.as_view(), name='change_password'),

    # Perfil
    path('me/', MeView.as_view(), name='me'),

    # Gestión de usuarios (solo Administrador)
    path('usuarios/', AdminUserListCreateView.as_view(), name='usuarios'),
    path('usuarios/<uuid:pk>/', AdminUserDetailView.as_view(), name='usuario-detalle'),
    # Válvula de escape de la sesión única: libera la cuenta de un usuario que
    # dejó la sesión colgada en un equipo al que ya no tiene acceso.
    path(
        'usuarios/<uuid:pk>/cerrar-sesion/',
        AdminCerrarSesionView.as_view(),
        name='usuario-cerrar-sesion',
    ),
]
