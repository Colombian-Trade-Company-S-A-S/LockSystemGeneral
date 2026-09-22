"""Lectura del portal WhaleTV con Selenium, como sustituto de la Device Lock API.

Existe porque las credenciales de la Device Lock API son POR ENTORNO: las de
producción no sirven contra ACC (`acc-saas.zeasn.tv` responde
`AUTHORIZATION ACCESSKEY NON-EXISTENT`). Mientras no haya llaves del entorno en
uso, todo lo que se leía por API se saca del portal web, que ya sabemos
controlar (ver `selenium_sync.py`).

Cubre lo mismo que `PortalClient` salvo el listado de códigos disponibles: el
portal no lo expone, solo permite pedir el Pin Code de UN passcode concreto
(botón "Generate Pin Code") y ver el historial de los ya entregados.

Coste: cada operación abre un Chromium y hace login (~25-40 s), frente a ~1 s
por API. Por eso todo pasa por `NAVEGADOR_LOCK`: dos Chromium simultáneos en
Render (~400 MB cada uno) tumbarían el servicio por falta de memoria.
"""
from __future__ import annotations

import contextlib
import re
import threading
import time

from django.conf import settings
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .client import PortalError
from .selenium_sync import (
    ResultadoSync,
    _abrir_detalle_por_mac,
    _esperar_carga,
    _leer_estado_remoto,
    abrir_sesion,
)

# Serializa el uso del navegador entre peticiones (memoria de Render).
NAVEGADOR_LOCK = threading.Lock()


class PortalCapacidadNoDisponible(PortalError):
    """La operación no se puede hacer por el portal (solo existe vía API)."""


class PortalPasscodeInvalido(PortalError):
    """El portal rechazó el passcode ("Incorrect passcode"): no existe, ya se
    usó, o no corresponde a ese dispositivo."""


class PortalScraper:
    """Mismas operaciones que `PortalClient`, pero raspando el portal.

    Se direcciona por MAC (no por EUI-64): el portal busca por MAC. Si se le
    pasan `driver`/`wait` reutiliza esa sesión en vez de abrir una nueva, que es
    lo que hace la validación masiva para no re-loguearse en cada televisor.
    """

    def __init__(self, driver=None, wait=None):
        self._driver = driver
        self._wait = wait

    # -- sesión ---------------------------------------------------------
    @contextlib.contextmanager
    def _sesion(self):
        """Devuelve (driver, wait). Si la sesión vino dada, no la abre ni cierra."""
        if self._driver is not None:
            yield self._driver, self._wait
            return
        with NAVEGADOR_LOCK:
            driver = None
            try:
                driver, wait = abrir_sesion()
                yield driver, wait
            finally:
                if driver is not None:
                    driver.quit()

    def _abrir_detalle(self, driver, wait, mac: str):
        res = ResultadoSync()
        cfg = settings.WHALETV_PORTAL
        if not _abrir_detalle_por_mac(driver, wait, cfg, mac, res):
            raise PortalError(f'No se encontró el MAC {mac} en el portal.')
        _esperar_carga(driver)
        return res

    # -- operaciones ----------------------------------------------------
    def get_status(self, mac: str) -> dict:
        """Equivalente a `PortalClient.get_status`, leyendo el detalle.

        El portal solo publica el Lock Status; `paymentStatus`/`clearStatus` no
        existen en esta vía y salen como None (quien los consuma debe tolerarlo).
        """
        with self._sesion() as (driver, wait):
            self._abrir_detalle(driver, wait, mac)
            res = ResultadoSync()
            bloqueado = _leer_estado_remoto(driver, res)
            if bloqueado is None:
                raise PortalError(
                    'No se pudo leer el Lock Status del dispositivo en el portal.'
                )
            return {
                'lockStatus': 1 if bloqueado else 0,
                'paymentStatus': None,
                'clearStatus': None,
            }

    def get_pin_codes(self, mac: str) -> list[dict]:
        raise PortalCapacidadNoDisponible(
            'El portal no publica la lista de códigos disponibles; solo permite '
            'pedir el Código Pin de un Código de Acceso concreto.'
        )

    def usar_pincode(self, mac: str, passcode: str) -> str:
        """Pide el Pin Code de `passcode` con el botón "Generate Pin Code".

        Devuelve el pinCode. El portal ya lo marca como usado al generarlo, así
        que no hace falta el equivalente a `marcar_pincodes_usados`.
        """
        with self._sesion() as (driver, wait):
            self._abrir_detalle(driver, wait, mac)

            boton = next(
                (
                    b
                    for b in driver.find_elements(By.TAG_NAME, 'button')
                    if b.is_displayed()
                    and 'generate pin' in b.text.strip().lower()
                ),
                None,
            )
            if boton is None:
                raise PortalError(
                    'No encontré el botón "Generate Pin Code" en el portal.'
                )

            driver.execute_script("arguments[0].click();", boton)
            dialogo = WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, '.el-dialog'))
            )
            _esperar_carga(driver)

            entrada = dialogo.find_element(By.CSS_SELECTOR, '.el-input__inner')
            entrada.clear()
            entrada.send_keys(passcode)

            enviar = dialogo.find_element(
                By.XPATH, ".//button[.//span[contains(text(),'Generate Pin Code')]]"
            )
            driver.execute_script("arguments[0].click();", enviar)

            # El portal responde de dos maneras:
            #   - deja el diálogo abierto con "Incorrect passcode" bajo el campo;
            #   - o lo cierra y pinta el panel "Your Pin Code: NNNNNN" arriba.
            # OJO: la fila del historial NO aparece de inmediato (el portal la
            # añade más tarde), así que el panel es la fuente fiable. El
            # historial queda solo como respaldo.
            fin = time.time() + 30
            while time.time() < fin:
                time.sleep(1)
                visibles = [
                    d
                    for d in driver.find_elements(By.CSS_SELECTOR, '.el-dialog')
                    if d.is_displayed()
                ]
                if visibles and 'incorrect passcode' in visibles[0].text.lower():
                    raise PortalPasscodeInvalido(
                        'El portal rechazó ese Código de Acceso '
                        '("Incorrect passcode").'
                    )
                pin = self._pin_generado(driver, passcode)
                if pin:
                    return pin
                pin = self._pin_en_historial(driver, passcode)
                if pin:
                    return pin

            raise PortalError(
                'El portal no devolvió el Código Pin a tiempo. Revisa el '
                'historial del dispositivo en el portal antes de reintentar: el '
                'código puede haberse generado igualmente.'
            )

    def historial_pincodes(self, mac: str) -> list[dict]:
        """Filas de "Passcode & Pin Code History" del dispositivo."""
        with self._sesion() as (driver, wait):
            self._abrir_detalle(driver, wait, mac)
            return self._leer_historial(driver)

    # -- utilidades -----------------------------------------------------
    @staticmethod
    def _pin_generado(driver, passcode: str) -> str | None:
        """Lee el panel "Your Pin Code" que el portal pinta tras generar.

        El panel es la única fuente inmediata: la fila del historial tarda en
        aparecer. Se exige que el "Passcode:" del propio panel coincida con el
        pedido, porque el panel sobrevive a generaciones anteriores y si no se
        comprobara se podría devolver el Pin de OTRO código de acceso.
        """
        try:
            texto = driver.find_element(By.TAG_NAME, 'body').text
        except Exception:  # noqa: BLE001
            return None

        # Recorta a la zona del panel: desde "Your Pin Code" hasta el historial.
        inicio = texto.lower().find('your pin code')
        if inicio == -1:
            return None
        fin = texto.lower().find('passcode & pin code history', inicio)
        panel = texto[inicio:fin if fin != -1 else inicio + 400]

        pin = re.search(r'your\s+pin\s+code\s*:?\s*([0-9]{4,12})', panel, re.I)
        suyo = re.search(r'passcode\s*:?\s*([0-9]{3,12})', panel, re.I)
        if not pin or not suyo:
            return None
        if suyo.group(1) != passcode:
            return None  # panel de una generación anterior
        return pin.group(1)

    @staticmethod
    def _leer_historial(driver) -> list[dict]:
        filas = []
        for tabla in driver.find_elements(By.CSS_SELECTOR, '.el-table'):
            cabeceras = [h.text.strip() for h in tabla.find_elements(By.CSS_SELECTOR, 'th')]
            if 'Passcode' not in cabeceras or 'Pin Code' not in cabeceras:
                continue
            for fila in tabla.find_elements(
                By.CSS_SELECTOR, '.el-table__body-wrapper .el-table__row'
            ):
                celdas = [c for c in fila.text.split('\n') if c.strip()]
                # NO. | Passcode | Pin Code | Date | Status
                if len(celdas) >= 3:
                    filas.append(
                        {
                            'passcode': celdas[1],
                            'pin_code': celdas[2],
                            'fecha': celdas[3] if len(celdas) > 3 else '',
                            'estado': celdas[4] if len(celdas) > 4 else '',
                        }
                    )
        return filas

    def _pin_en_historial(self, driver, passcode: str) -> str | None:
        for fila in self._leer_historial(driver):
            if fila['passcode'] == passcode and fila['pin_code']:
                return fila['pin_code']
        return None
