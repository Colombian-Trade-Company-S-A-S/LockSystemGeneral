"""Cliente de la WhaleTV *Device Lock Portal API* (Open API 1.0.1).

OJO: no confundir con `client.PortalClient`. Son dos servicios distintos, con
credenciales distintas:

    client.PortalClient      -> Device Lock Service API  (saas.zeasn.tv)
                                Solo GET. Lee estado y códigos. NO puede bloquear.
    open_client.PortalOpenClient -> Device Lock Portal API (lockservice.whaletv.com)
                                GET + POST. Es la API del MISMO portal web que
                                hoy raspamos con Selenium, así que SÍ bloquea.

Autenticación (idéntica a la otra API):
    ts        = timestamp actual en milisegundos
    signature = Base64(HMAC_SHA1(secretKey, requestURI + ts))
    Authorization = accessKey + ":" + signature + ":" + ts

`requestURI` es la ruta firmada **sin host y sin query string**, e incluye el
prefijo completo: `/lock-portal/open/v1/devices/import`. El body JSON de los
POST NO entra en la firma.

Todos los endpoints exigen `brandId`. Se toma de la configuración salvo que se
pase explícito (lo necesita el descubrimiento por tanteo del brandId).

Este módulo no lo usa nadie todavía: convive con Selenium hasta que se valide
contra ACC. Solo stdlib.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


class PortalOpenError(Exception):
    """Fallo de comunicación o respuesta de error de la Portal API."""


class PortalOpenAuthError(PortalOpenError):
    """401: firma, accessKey, timestamp o permisos. Trae el texto del servidor."""


class PortalOpenBrandNoAutorizado(PortalOpenError):
    """270202: el brandId no pertenece a este accessKey."""


class PortalOpenDispositivoNoExiste(PortalOpenError):
    """270102: el dispositivo no está dado de alta en el portal."""


class PortalOpenMacInvalida(PortalOpenError):
    """270104: la MAC no tiene el formato que espera el portal."""


class PortalOpenSinPincode(PortalOpenError):
    """270103: no hay Código Pin disponible para ese dispositivo."""


class PortalOpenParametros(PortalOpenError):
    """270201: faltan parámetros o son ilegales."""


class PortalOpenLoteExcedido(PortalOpenError):
    """270203: el lote supera el máximo (1000 elementos)."""


# errorCode -> excepción. Los que no estén aquí caen en PortalOpenError.
_ERRORES = {
    '270201': PortalOpenParametros,
    '270202': PortalOpenBrandNoAutorizado,
    '270203': PortalOpenLoteExcedido,
    '270102': PortalOpenDispositivoNoExiste,
    '270103': PortalOpenSinPincode,
    '270104': PortalOpenMacInvalida,
}

# Tope de elementos por lote en import y batch-lock (doc §4.4 y §4.6).
MAX_LOTE = 1000

# El portal devuelve 503 a ratos ("upstream connect error… connection refused")
# y se recupera en segundos. Solo se reintentan las LECTURAS; ver `_enviar`.
HTTP_PASAJEROS = (502, 503, 504)
REINTENTOS_LECTURA = 3
ESPERA_REINTENTO = 1.5  # segundos, se multiplica por el número de intento


# -- conversiones tolerantes ------------------------------------------------
# La doc avisa: "Numeric and Boolean values may be serialized as JSON strings".
# O sea que `status` puede llegar como 1 o como "1", y `data` como true o "true".

def _int(valor, por_defecto=None):
    if valor is None or valor == '':
        return por_defecto
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return por_defecto


def _bool(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ('true', '1', 'yes')


def _str(valor, por_defecto='') -> str:
    return por_defecto if valor is None else str(valor)


class PortalOpenClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or settings.WHALETV_LOCK_PORTAL_API

    # -- autenticación --------------------------------------------------
    def _authorization(self, request_uri: str) -> str:
        ts = str(int(time.time() * 1000))
        digest = hmac.new(
            self.cfg['SECRET_KEY'].encode('utf-8'),
            (request_uri + ts).encode('utf-8'),
            hashlib.sha1,
        ).digest()
        signature = base64.b64encode(digest).decode('utf-8')
        return f"{self.cfg['ACCESS_KEY']}:{signature}:{ts}"

    def _brand(self, brand_id=None):
        valor = brand_id if brand_id is not None else self.cfg.get('BRAND_ID')
        if valor in (None, ''):
            raise PortalOpenError(
                'Falta el brandId: define WHALETV_LOCK_PORTAL_API_BRAND_ID '
                '(descúbrelo con `manage.py probar_portal_open --descubrir-brand`).'
            )
        return valor

    # -- llamada base ---------------------------------------------------
    def _peticion(self, path: str, params: dict | None = None,
                  body: dict | None = None) -> dict:
        """Devuelve el cuerpo completo (no solo `data`): la paginación mete
        `totalSize` en la raíz."""
        request_uri = f"{self.cfg['API_BASE']}{path}"  # lo que se firma
        url = f"https://{self.cfg['HOST']}{request_uri}"
        if params:
            limpios = {k: v for k, v in params.items() if v not in (None, '')}
            url += '?' + urllib.parse.urlencode(limpios)

        datos = None
        if body is not None:
            datos = json.dumps(body).encode('utf-8')

        crudo = self._enviar(url, request_uri, datos)

        try:
            cuerpo = json.loads(crudo)
        except json.JSONDecodeError as e:
            raise PortalOpenError(f'El portal no devolvió JSON: {crudo[:300]}') from e

        codigo = _str(cuerpo.get('errorCode'), '')
        if codigo not in ('0', ''):
            mensaje = cuerpo.get('errorMsg') or f'errorCode {codigo}'
            raise _ERRORES.get(codigo, PortalOpenError)(f'{mensaje} ({codigo})')
        return cuerpo

    def _enviar(self, url: str, request_uri: str, datos: bytes | None) -> str:
        """Manda la petición y devuelve el cuerpo crudo.

        Reintenta los fallos pasajeros (502/503/504 y caídas de conexión) **solo
        en las lecturas**. El portal de WhaleTV devuelve 503 a ratos —
        "upstream connect error… connection refused"— y se recupera al segundo o
        tercer intento. En las escrituras NO se reintenta: `POST /devices/pincode`
        no es idempotente y un segundo intento quemaría otro código. Para esas,
        el fallo sube y lo recoge el respaldo con Selenium.
        """
        es_lectura = datos is None
        intentos = REINTENTOS_LECTURA if es_lectura else 1

        for intento in range(1, intentos + 1):
            # La firma lleva el timestamp dentro, así que se recalcula en cada
            # intento: reutilizarla acercaría la petición al límite de 1 hora.
            req = urllib.request.Request(
                url, data=datos, method='GET' if es_lectura else 'POST'
            )
            req.add_header('Authorization', self._authorization(request_uri))
            req.add_header('Accept', 'application/json')
            if not es_lectura:
                req.add_header('Content-Type', 'application/json')

            try:
                with urllib.request.urlopen(
                    req, timeout=self.cfg.get('TIMEOUT', 20)
                ) as resp:
                    return resp.read().decode('utf-8')
            except urllib.error.HTTPError as e:
                detalle = e.read().decode('utf-8', errors='replace').strip()
                # Los fallos de autenticación llegan como 401 text/html, no JSON.
                if e.code == 401:
                    raise PortalOpenAuthError(detalle or 'HTTP 401 sin cuerpo') from e
                if e.code in HTTP_PASAJEROS and intento < intentos:
                    time.sleep(ESPERA_REINTENTO * intento)
                    continue
                raise PortalOpenError(f'HTTP {e.code} del portal: {detalle}') from e
            except urllib.error.URLError as e:
                if intento < intentos:
                    time.sleep(ESPERA_REINTENTO * intento)
                    continue
                raise PortalOpenError(
                    f'No se pudo conectar con el portal: {e.reason}'
                ) from e


    # -- 4.1 listado ----------------------------------------------------
    def listar_dispositivos(self, *, brand_id=None, mac=None, sn=None, status=None,
                            next_installment_date=None, due_status=None,
                            payment_status=None, page_num=1,
                            page_size=20) -> tuple[list[dict], int]:
        """Devuelve (dispositivos, total). page_size máximo 200."""
        cuerpo = self._peticion('/devices', {
            'brandId': self._brand(brand_id),
            'mac': mac,
            'sn': sn,
            'status': status,
            'nextInstallmentDate': next_installment_date,
            'dueStatus': due_status,
            'paymentStatus': payment_status,
            'pageNum': page_num,
            'pageSize': page_size,
        })
        items = [self._dispositivo(d) for d in (cuerpo.get('data') or [])]
        return items, _int(cuerpo.get('totalSize'), 0)

    def buscar_por_mac(self, mac: str, *, brand_id=None) -> dict | None:
        """El `id` que piden detail/update/clear. None si el portal no lo tiene."""
        items, _ = self.listar_dispositivos(mac=mac, brand_id=brand_id, page_size=1)
        return items[0] if items else None

    # -- 4.2 detalle ----------------------------------------------------
    def detalle(self, device_id, *, brand_id=None) -> dict:
        cuerpo = self._peticion('/devices/detail', {
            'id': device_id,
            'brandId': self._brand(brand_id),
        })
        return self._dispositivo(cuerpo.get('data') or {}, detalle=True)

    # -- 4.3 historial de pincodes --------------------------------------
    def historial_pincodes(self, device_id, *, brand_id=None) -> list[dict]:
        cuerpo = self._peticion('/devices/pincode-history', {
            'id': device_id,
            'brandId': self._brand(brand_id),
        })
        return [
            {
                'passcode': _str(g.get('passcode')),
                'pincode': _str(g.get('pincode')),
                'date': _int(g.get('date')),
                # 0 sin usar, 1 emitido, 2 usado
                'status': _int(g.get('status')),
            }
            for g in (cuerpo.get('data') or [])
        ]

    # -- 4.4 alta masiva ------------------------------------------------
    def importar(self, items: list[str], *, es_serial=False, brand_id=None) -> dict:
        self._validar_lote(items, 'items')
        cuerpo = self._peticion('/devices/import', body={
            'brandId': self._brand(brand_id),
            'isSn': 1 if es_serial else 0,
            'items': items,
        })
        data = cuerpo.get('data') or {}
        return {
            'success': _int(data.get('success'), 0),
            'fail': _int(data.get('fail'), 0),
            'batchId': _int(data.get('batchId')),
        }

    # -- 4.5 actualizar -------------------------------------------------
    def actualizar(self, device_id, *, status=None, payment_status=None,
                   next_installment_date=None, installment_cycle=None,
                   brand_id=None) -> bool:
        campos = {
            'status': status,
            'paymentStatus': payment_status,
            'nextInstallmentDate': next_installment_date,
            'installmentCycle': installment_cycle,
        }
        campos = {k: v for k, v in campos.items() if v is not None}
        if not campos:
            raise PortalOpenParametros(
                'Hay que enviar al menos uno de status, paymentStatus, '
                'nextInstallmentDate o installmentCycle.'
            )
        cuerpo = self._peticion('/devices/update', body={
            'brandId': self._brand(brand_id), 'id': device_id, **campos,
        })
        return _bool(cuerpo.get('data'))

    # -- 4.6 bloqueo masivo ---------------------------------------------
    def bloquear(self, macs: list[str], *, bloquear=True, brand_id=None) -> bool:
        """Lo que Selenium no podía hacer: bloquear de verdad, hasta 1000 de golpe."""
        self._validar_lote(macs, 'macs')
        cuerpo = self._peticion('/devices/batch-lock', body={
            'brandId': self._brand(brand_id),
            'macs': macs,
            'status': 1 if bloquear else 0,
        })
        return _bool(cuerpo.get('data'))

    # -- 4.7 generar pincode --------------------------------------------
    def generar_pincode(self, mac: str, passcode: str, *, brand_id=None) -> str:
        """El equivalente al botón "Generate Pin Code" del portal, en 1 llamada."""
        cuerpo = self._peticion('/devices/pincode', body={
            'brandId': self._brand(brand_id),
            'mac': mac,
            'passcode': passcode,
        })
        return _str((cuerpo.get('data') or {}).get('pincode'))

    # -- 4.8 limpiar ----------------------------------------------------
    def limpiar(self, device_id, *, brand_id=None) -> bool:
        cuerpo = self._peticion('/devices/clear', body={
            'brandId': self._brand(brand_id), 'id': device_id,
        })
        return _bool(cuerpo.get('data'))

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _validar_lote(valores, nombre):
        if not valores:
            raise PortalOpenParametros(f'`{nombre}` no puede ir vacío.')
        if len(valores) > MAX_LOTE:
            raise PortalOpenLoteExcedido(
                f'`{nombre}` trae {len(valores)} elementos; el máximo es {MAX_LOTE}.'
            )

    @staticmethod
    def _dispositivo(d: dict, detalle=False) -> dict:
        """Normaliza un dispositivo. Los campos extra solo vienen en /detail."""
        salida = {
            'id': _int(d.get('id')),
            'mac': _str(d.get('mac')),
            'sn': _str(d.get('sn')),
            # 0 desbloqueado, 1 bloqueado
            'status': _int(d.get('status')),
            'lastOnlineTime': _int(d.get('lastOnlineTime')),
            'importTime': _int(d.get('importTime')),
            'nextInstallmentDate': _str(d.get('nextInstallmentDate')),
            # 0 al día, 1 vencido
            'dueStatus': _int(d.get('dueStatus')),
            # 0 en curso, 1 completo
            'paymentStatus': _int(d.get('paymentStatus')),
        }
        if detalle:
            salida.update({
                'brandId': _int(d.get('brandId')),
                'installmentCycle': _str(d.get('installmentCycle')),
                'curPincode': _str(d.get('curPincode')),
                'curPasscode': _str(d.get('curPasscode')),
                # 0 normal, 1 limpiando
                'clearStatus': _int(d.get('clearStatus')),
            })
        return salida
