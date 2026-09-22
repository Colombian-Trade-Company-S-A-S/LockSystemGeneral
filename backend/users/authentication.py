"""Autenticación JWT con revocación server-side.

SimpleJWT es stateless: un token válido lo es hasta que expira. Para poder
"cerrar sesión de verdad" (invalidar tokens ya emitidos) sin depender de la app
`token_blacklist` (bloqueada en este entorno por el límite de rutas de Windows),
cada JWT lleva la **versión de token** con la que se emitió (claim `tv`). El
logout incrementa `user.token_version`, de modo que todos los tokens anteriores
dejan de coincidir. Es exacto: no tiene la ambigüedad de segundos que tendría
comparar por fecha de emisión (`iat`).
"""
from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from users.session import (
    dispositivo_ajeno,
    dispositivo_de,
    registrar_actividad,
    sesion_expirada,
)

# Nombre del claim que transporta la versión de token.
TOKEN_VERSION_CLAIM = 'tv'


def token_esta_revocado(user, validated_token) -> bool:
    """True si la versión del token no coincide con la vigente del usuario."""
    return validated_token.get(TOKEN_VERSION_CLAIM) != getattr(user, 'token_version', 0)


def empresa_inactiva(user) -> bool:
    """True si el usuario pertenece a una empresa desactivada.

    Desactivar la empresa corta el acceso de toda su gente sin tener que ir
    usuario por usuario. Se comprueba en cada petición y no solo en el login:
    de lo contrario, un token ya emitido seguiría entrando hasta expirar.
    """
    empresa = getattr(user, 'empresa', None)
    return empresa is not None and not empresa.activa


class RevocationAwareJWTAuthentication(JWTAuthentication):
    """JWTAuthentication que respeta revocación, inactividad y sesión única."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if token_esta_revocado(user, validated_token):
            raise InvalidToken('La sesión fue cerrada. Inicia sesión de nuevo.')
        if empresa_inactiva(user):
            raise InvalidToken('La empresa de tu cuenta está desactivada.')
        if sesion_expirada(user):
            raise InvalidToken('Tu sesión se cerró por inactividad.')
        return user

    def authenticate(self, request):
        """Añade la comprobación del navegador a la autenticación normal.

        Va aquí y no en `get_user` porque es lo primero que ve la petición
        completa (con sus cabeceras). Un token que se lleven a otro equipo se
        queda fuera: no basta con el token, tiene que llegar desde el mismo
        navegador donde se abrió la sesión.
        """
        resultado = super().authenticate(request)
        if resultado is None:
            return None

        user, _token = resultado
        if dispositivo_ajeno(user, dispositivo_de(request)):
            raise InvalidToken(
                'Tu sesión está abierta en otro dispositivo. Inicia sesión de nuevo.'
            )
        # La petición cuenta como actividad: renueva el plazo para la siguiente.
        registrar_actividad(user)
        return resultado
