"""Diagnóstico en vivo de la Device Lock Portal API, para la pantalla de
Configuración.

Existe porque la vinculación *accessKey → marca* del lado de Zeasn se ha caído
dos veces tras despliegues suyos, y el síntoma es que TODO deja de funcionar sin
aviso. Con este botón se comprueba en tres segundos si el problema es nuestro o
de ellos, sin abrir una terminal.

Es de solo lectura: pide un listado de una página y ya. No bloquea ni modifica
ningún televisor.
"""
from __future__ import annotations

import time

from django.conf import settings
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from televisores.portal import open_sync
from televisores.portal.open_client import (
    PortalOpenAuthError,
    PortalOpenBrandNoAutorizado,
    PortalOpenClient,
    PortalOpenError,
)

# Un brandId cualquiera que no es nuestro. Sirve para distinguir los dos
# sabores del 270202: "no permission for this brand" (la vinculación existe,
# sólo que esa marca no es nuestra) de "no brand authorized for this accessKey"
# (nuestro accessKey se quedó sin ninguna marca: la regresión de Zeasn).
BRAND_AJENO = '1'


class PuedeDiagnosticar(BasePermission):
    """Solo los correos de `DIAGNOSTICO_API_EMAILS`.

    Es una herramienta de soporte, no una función del producto: expone detalles
    de la integración (host, brandId, mensajes crudos del proveedor) que no le
    interesan —ni le convienen— al resto de usuarios.
    """

    message = 'No tienes acceso al diagnóstico de la API.'

    def has_permission(self, request, view):
        correo = (getattr(request.user, 'email', '') or '').strip().lower()
        return bool(correo) and correo in settings.DIAGNOSTICO_API_EMAILS


class DiagnosticoApiView(APIView):
    """GET /api/diagnostico-api/ -> estado de la integración con WhaleTV."""

    permission_classes = [IsAuthenticated, PuedeDiagnosticar]

    def get(self, request):
        cfg = settings.WHALETV_LOCK_PORTAL_API
        pruebas = []

        # 1. Configuración -------------------------------------------------
        faltantes = [
            nombre
            for nombre, clave in (
                ('Access Key', 'ACCESS_KEY'),
                ('Secret Key', 'SECRET_KEY'),
                ('Brand ID', 'BRAND_ID'),
            )
            if not cfg.get(clave)
        ]
        if faltantes:
            pruebas.append(_prueba(
                'Configuración', False,
                'Faltan credenciales: ' + ', '.join(faltantes),
            ))
            return Response(_respuesta(cfg, pruebas))

        if not cfg.get('ENABLED'):
            pruebas.append(_prueba(
                'Configuración', False,
                'La API está apagada (WHALETV_LOCK_PORTAL_API_ENABLED=false). '
                'Todo se está haciendo con Selenium.',
            ))
        else:
            pruebas.append(_prueba(
                'Configuración', True, 'Credenciales completas y API activada.'
            ))

        cliente = PortalOpenClient()

        # 2. Conexión + autenticación + permiso de marca --------------------
        # Una sola llamada responde las tres preguntas, según cómo falle.
        inicio = time.monotonic()
        try:
            items, total = cliente.listar_dispositivos(page_size=1)
            ms = int((time.monotonic() - inicio) * 1000)
            pruebas.append(_prueba('Conexión', True, f'{cfg["HOST"]} respondió.', ms))
            pruebas.append(_prueba('Autenticación', True, 'Firma aceptada.'))
            pruebas.append(_prueba(
                'Permiso de marca', True, f'Marca {cfg["BRAND_ID"]} autorizada.'
            ))
            pruebas.append(_prueba(
                'Lectura de dispositivos', True,
                f'{total} dispositivo(s) en la marca.', ms,
            ))
        except PortalOpenAuthError as e:
            pruebas.append(_prueba('Conexión', True, f'{cfg["HOST"]} respondió.'))
            pruebas.append(_prueba(
                'Autenticación', False,
                f'El portal rechazó las credenciales: {e}. '
                'Revisa el Access Key y el Secret Key.',
            ))
        except PortalOpenBrandNoAutorizado as e:
            pruebas.append(_prueba('Conexión', True, f'{cfg["HOST"]} respondió.'))
            pruebas.append(_prueba('Autenticación', True, 'Firma aceptada.'))
            pruebas.append(_prueba(
                'Permiso de marca', False, _explicar_marca(cliente, e),
            ))
        except PortalOpenError as e:
            pruebas.append(_prueba('Conexión', False, str(e)))

        return Response(_respuesta(cfg, pruebas))


def _explicar_marca(cliente, error) -> str:
    """Distingue "esta marca no es tuya" de "no tienes ninguna marca"."""
    texto = str(error).lower()
    if 'no brand authorized' in texto:
        return (
            'WhaleTV dice que este Access Key no tiene NINGUNA marca asociada. '
            'Ya pasó dos veces tras despliegues suyos: hay que pedirle a Zeasn '
            'que vuelva a vincular la marca RCA al Access Key.'
        )

    # El mensaje no es el conocido: se comprueba con una marca ajena para ver
    # si el problema es el brandId configurado o la vinculación entera.
    try:
        cliente.listar_dispositivos(brand_id=BRAND_AJENO, page_size=1)
    except PortalOpenBrandNoAutorizado as ajeno:
        if 'no brand authorized' in str(ajeno).lower():
            return (
                'Este Access Key se quedó sin marcas asociadas del lado de '
                'WhaleTV. Hay que pedirle a Zeasn que las vuelva a vincular.'
            )
    except PortalOpenError:
        pass
    return (
        f'El Brand ID configurado ({error}). Revisa '
        'WHALETV_LOCK_PORTAL_API_BRAND_ID.'
    )


def _prueba(nombre, ok, detalle, ms=None) -> dict:
    return {'nombre': nombre, 'ok': ok, 'detalle': detalle, 'ms': ms}


def _respuesta(cfg, pruebas) -> dict:
    ok = all(p['ok'] for p in pruebas)
    return {
        'ok': ok,
        'usa_api': open_sync.usa_open(),
        'host': cfg.get('HOST', ''),
        'brand_id': cfg.get('BRAND_ID', ''),
        'access_key': _enmascarar(cfg.get('ACCESS_KEY', '')),
        'pruebas': pruebas,
        'resumen': (
            'La API de WhaleTV está funcionando.'
            if ok
            else 'La API de WhaleTV no está funcionando. '
                 'Los bloqueos se intentarán con Selenium.'
        ),
    }


def _enmascarar(clave: str) -> str:
    """El Access Key es un identificador público, pero no hace falta enseñarlo
    entero en pantalla."""
    if len(clave) <= 12:
        return clave
    return f'{clave[:8]}…{clave[-4:]}'
