"""API de integración (máquina-a-máquina), direccionada por SERIAL.

Es el mismo TelevisorViewSet del panel, pero:
  - se accede por `serial_number` en la URL, no por la PK de la base (los
    integradores nos compran por serial y no conocen nuestros ids);
  - se autentica con API-key por empresa (no con JWT de usuario);
  - queda acotada, como todo, a la empresa de la API-key.

No se reimplementa nada: se hereda toda la lógica (estados, pines, validación,
exportes…). El aislamiento por empresa funciona igual porque el usuario
sintético de la API-key lleva su `empresa` (ver empresas/authentication.py).
"""
from __future__ import annotations

from empresas.authentication import ApiKeyAuthentication, ApiKeyRateThrottle

from .views import TelevisorViewSet


class IntegracionTelevisorViewSet(TelevisorViewSet):
    # Solo API-key: esta superficie no acepta el JWT del panel.
    authentication_classes = [ApiKeyAuthentication]
    throttle_classes = [ApiKeyRateThrottle]

    # Se busca por serial. `lookup_url_kwarg='pk'` mantiene el nombre del
    # parámetro que ya usan todas las @action heredadas (que reciben `pk`), así
    # que solo cambia POR QUÉ columna se resuelve, no el resto del código.
    lookup_field = 'serial_number'
    lookup_url_kwarg = 'pk'
    # El serial puede traer letras, dígitos y guiones; se admite cualquier cosa
    # menos la barra (que separa segmentos de la URL).
    lookup_value_regex = '[^/]+'

    def _resumen_enrolar_masivo(self, request, empresa):
        """En integración TODO es por serial: el cambio de estado masivo
        identifica cada televisor por su "serial_number", no por MAC. Solo
        actualiza equipos existentes (crearlos necesita la MAC; ver A.3)."""
        from televisores.estado_import import procesar_registros_por_serial

        if 'registros' in request.data:
            return procesar_registros_por_serial(
                request.data.get('registros'), empresa=empresa
            )
        return None

    def _lanzar_validacion_masiva(self, request, empresa):
        """En integración la validación masiva también es por SERIAL: valida solo
        los seriales del JSON `registros` (formato `[{"serial_number": ...}]`).
        Si no viene `registros`, cae al comportamiento base (validar todos)."""
        from televisores.bulk_sync import lanzar_validacion_por_serial

        if 'registros' in request.data:
            return lanzar_validacion_por_serial(
                request.data.get('registros'), empresa=empresa
            )
        return super()._lanzar_validacion_masiva(request, empresa)
