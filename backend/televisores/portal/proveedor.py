"""De dónde se leen el estado y los Códigos Pin: de la API o del portal web.

Hay dos implementaciones equivalentes:

    Device Lock API (`client.PortalClient`)  -> rápida (~1 s), completa.
    Portal web      (`scraper.PortalScraper`) -> lenta (~30 s), incompleta.

Se elige SOLA según la configuración: si `WHALETV_LOCK_API_ACCESS_KEY` y
`WHALETV_LOCK_API_SECRET_KEY` tienen valor se usa la API; si están vacías se
cae al portal. Así, el día que lleguen las llaves del entorno correcto basta con
pegarlas en el `.env` (y en Render) para volver al modo rápido sin tocar código.

Diferencias del modo portal, que quien consuma esto debe tolerar:
  - `paymentStatus` y `clearStatus` salen como None (el portal no los publica).
  - `pin_codes()` lanza PortalCapacidadNoDisponible (el portal no lista la bolsa
    de códigos; solo resuelve un passcode concreto).

Ver `MODO_PORTAL.md` en la raíz del backend.
"""
from __future__ import annotations

import contextlib

from django.conf import settings

from .client import PortalClient
from .open_sync import usa_open
from .scraper import PortalScraper


def usa_portal() -> bool:
    """True si toca leer del portal (no hay credenciales de la Device Lock API)."""
    cfg = settings.WHALETV_LOCK_API
    return not (cfg.get('ACCESS_KEY') and cfg.get('SECRET_KEY'))


def modo() -> str:
    """'open', 'portal' o 'api'. Se expone en las respuestas para no adivinar."""
    if usa_open():
        return 'open'
    return 'portal' if usa_portal() else 'api'


class _ProveedorApi:
    """Adaptador sobre PortalClient. Direcciona por EUI-64."""

    def __init__(self):
        self._client = PortalClient()

    def get_status(self, tv) -> dict:
        return self._client.get_status(tv.eui64_portal)

    def get_pin_codes(self, tv) -> list[dict]:
        return self._client.get_pin_codes(tv.eui64_portal)

    def usar_pincode(self, tv, passcode: str) -> str:
        grupos = self._client.get_pin_codes(tv.eui64_portal)
        grupo = next((g for g in grupos if g['passCode'] == passcode), None)
        if grupo is None:
            from .scraper import PortalPasscodeInvalido

            raise PortalPasscodeInvalido(
                'No hay un Código Pin disponible para ese Código de Acceso.'
            )
        # Marcar como usado es best-effort: si falla, el pin ya se entregó.
        from .client import PortalError

        try:
            self._client.marcar_pincodes_usados(tv.eui64_portal, [passcode])
        except PortalError:
            pass
        return grupo['pinCode']


class _ProveedorOpen:
    """Adaptador sobre la Portal API (open_client). Direcciona por MAC.

    Cubre el estado y los Códigos Pin. Si la API falla por un problema de
    servicio, cae al proveedor de siempre (Selenium o la otra API).
    """

    def __init__(self):
        from .open_client import PortalOpenClient

        self._client = PortalOpenClient()
        self._respaldo = _ProveedorPortal() if usa_portal() else _ProveedorApi()

    def get_status(self, tv) -> dict:
        from .client import PortalDispositivoNoExiste
        from .open_client import PortalOpenError
        from .open_sync import merece_respaldo

        try:
            encontrado = self._client.buscar_por_mac(tv.mac_address)
            if not encontrado:
                raise PortalDispositivoNoExiste(
                    f'El MAC {tv.mac_address} no está registrado en el portal WhaleTV.'
                )
            detalle = self._client.detalle(encontrado['id'])
            return {
                'lockStatus': detalle['status'],
                'paymentStatus': detalle['paymentStatus'],
                'clearStatus': detalle['clearStatus'],
            }
        except PortalOpenError as e:
            # Si el servicio falla, se lee por el camino de siempre. Es más
            # lento, pero el operador ve el estado igual.
            if not merece_respaldo(e):
                raise
            return self._respaldo.get_status(tv)

    # -- Códigos Pin -----------------------------------------------------
    def get_pin_codes(self, tv) -> list[dict]:
        """La Portal API no publica la bolsa de códigos disponibles.

        Solo tiene el historial de los ya entregados y la generación de uno
        concreto, igual que el portal web. Se delega al proveedor de siempre,
        que ya sabe responder a esto (en modo portal levanta
        `PortalCapacidadNoDisponible` sin abrir el navegador).
        """
        return self._respaldo.get_pin_codes(tv)

    def usar_pincode(self, tv, passcode: str) -> str:
        """Resuelve el Código de Acceso del televisor a su Código Pin.

        `POST /devices/pincode` hace en ~1 s lo que a Selenium le cuesta ~15 s
        con el botón "Generate Pin Code", y ya lo deja marcado como usado.

        No hace falta comprobar que el pin devuelto corresponda al passcode
        pedido, como sí hace el scraper: allí se lee un panel compartido que
        sobrevive a generaciones anteriores, mientras que aquí la respuesta es
        del propio passcode que se envió.
        """
        from .client import PortalDispositivoNoExiste
        from .open_client import (
            PortalOpenDispositivoNoExiste,
            PortalOpenError,
            PortalOpenMacInvalida,
            PortalOpenSinPincode,
        )
        from .open_sync import merece_respaldo
        from .scraper import PortalPasscodeInvalido

        try:
            pin = self._client.generar_pincode(tv.mac_address, passcode)
        except PortalOpenSinPincode as e:
            # 270103: el Código de Acceso no es válido o ya se usó. Es la misma
            # situación que el "Incorrect passcode" del portal web.
            raise PortalPasscodeInvalido(
                'No hay un Código Pin disponible para ese Código de Acceso.'
            ) from e
        except PortalOpenDispositivoNoExiste as e:
            raise PortalDispositivoNoExiste(str(e)) from e
        except PortalOpenMacInvalida as e:
            # La vista traduce ValueError a "MAC inválida", igual que cuando
            # falla la conversión a EUI-64.
            raise ValueError(str(e)) from e
        except PortalOpenError as e:
            if not merece_respaldo(e):
                raise
            return self._respaldo.usar_pincode(tv, passcode)

        if not pin:
            # Respuesta vacía sin error: no se puede entregar un pin en blanco.
            return self._respaldo.usar_pincode(tv, passcode)
        return pin


class _ProveedorPortal:
    """Adaptador sobre PortalScraper. Direcciona por MAC."""

    def __init__(self, driver=None, wait=None):
        self._scraper = PortalScraper(driver=driver, wait=wait)

    def get_status(self, tv) -> dict:
        return self._scraper.get_status(tv.mac_address)

    def get_pin_codes(self, tv) -> list[dict]:
        return self._scraper.get_pin_codes(tv.mac_address)

    def usar_pincode(self, tv, passcode: str) -> str:
        return self._scraper.usar_pincode(tv.mac_address, passcode)


def proveedor():
    """El proveedor que toque según la configuración."""
    if usa_open():
        return _ProveedorOpen()
    return _ProveedorPortal() if usa_portal() else _ProveedorApi()


@contextlib.contextmanager
def sesion_proveedor():
    """Proveedor para procesos por lotes.

    En modo portal abre UN navegador para todo el lote (una sola vez el login)
    en vez de uno por televisor; en los modos API es el proveedor normal.
    """
    if usa_open():
        yield _ProveedorOpen()
        return

    if not usa_portal():
        yield _ProveedorApi()
        return

    from .scraper import NAVEGADOR_LOCK
    from .selenium_sync import abrir_sesion

    with NAVEGADOR_LOCK:
        driver = None
        try:
            driver, wait = abrir_sesion()
            yield _ProveedorPortal(driver=driver, wait=wait)
        finally:
            if driver is not None:
                driver.quit()
