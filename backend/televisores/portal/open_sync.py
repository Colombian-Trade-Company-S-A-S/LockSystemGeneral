"""Bloquear / desbloquear por la Device Lock **Portal API**, sin navegador.

Es el reemplazo de `selenium_sync` para el camino de escritura. Expone las
mismas funciones y devuelve el mismo `ResultadoSync`, así que quien las llama
(`sync_runner`, `bulk_sync`) no cambia de forma.

Diferencias con Selenium:
  - `batch-lock` bloquea hasta 1000 MAC en UNA llamada, en vez de un navegador
    por televisor. Un lote entero pasa de minutos a segundos.
  - No hay login, ni `NAVEGADOR_LOCK`, ni ~400 MB de Chromium por proceso.

Sobre la fecha (Next Installment Date): se sigue empujando igual que en
Selenium, aunque `batch-lock` ya bloquea por sí solo. Zeasn confirmó que tienen
un cron que revisa esa fecha y cambia el estado del dispositivo; como
`fecha_sincronizar` es pasada al inhabilitar y futura al habilitar, mandarla
deja al cron **de acuerdo** con lo que acabamos de hacer en vez de en contra.
Si algún día se confirma que el cron no interfiere, se puede dejar de enviar
pasando `sincronizar_fecha=False`.
"""
from __future__ import annotations

from django.conf import settings

from .open_client import (
    MAX_LOTE,
    PortalOpenClient,
    PortalOpenDispositivoNoExiste,
    PortalOpenError,
    PortalOpenLoteExcedido,
    PortalOpenMacInvalida,
    PortalOpenParametros,
    PortalOpenSinPincode,
)
from .selenium_sync import ResultadoSync

# La API exige MM/dd/yyyy (doc 1.0.2). Ojo: el ejemplo del PDF trae guiones
# ("09-27-2026") pero la API los rechaza; verificado contra ACC el 2026-08-31.
FORMATO_FECHA = '%m/%d/%Y'

# Fallos del DATO, no del servicio: reintentar por Selenium daría el mismo
# resultado 15 s más tarde. Todo lo demás —caída de red, 500, 401, la
# vinculación de marca que Zeasn ha perdido dos veces— sí merece el respaldo.
ERRORES_DE_NEGOCIO = (
    PortalOpenDispositivoNoExiste,
    PortalOpenMacInvalida,
    PortalOpenParametros,
    PortalOpenLoteExcedido,
    PortalOpenSinPincode,
)


def merece_respaldo(exc: Exception) -> bool:
    """True si vale la pena reintentar la operación por Selenium."""
    return not isinstance(exc, ERRORES_DE_NEGOCIO)


def usa_open() -> bool:
    """True si el modo API-portal está activo y bien configurado.

    Hace falta el interruptor explícito Y las tres credenciales: con la config a
    medias es preferible seguir por Selenium que fallar en cada operación.
    """
    cfg = settings.WHALETV_LOCK_PORTAL_API
    return bool(
        cfg.get('ENABLED')
        and cfg.get('ACCESS_KEY')
        and cfg.get('SECRET_KEY')
        and cfg.get('BRAND_ID')
    )


def _fecha(televisor) -> str:
    return televisor.fecha_sincronizar.strftime(FORMATO_FECHA)


def intentar(televisor, sincronizar_fecha=True, progreso=None):
    """Aplica el estado de UN televisor. Devuelve `(resultado, usar_respaldo)`.

    `usar_respaldo` dice si conviene reintentar por Selenium. Es la forma que
    usa `sync_runner`; `sincronizar_estado()` es la envoltura para quien solo
    quiere el resultado.
    """
    def avisar(pct, msg=''):
        if progreso:
            progreso(pct, msg)

    res = ResultadoSync()
    cliente = PortalOpenClient()
    try:
        # Primero hay que confirmar que el equipo existe: `batch-lock` acepta
        # MACs desconocidas y responde `true` sin hacer nada (comprobado contra
        # ACC). Sin esta comprobación, un MAC mal escrito se reportaría como
        # bloqueado. Selenium sí falla en ese caso, y aquí se mantiene igual.
        avisar(10, 'Buscando el televisor…')
        encontrado = cliente.buscar_por_mac(televisor.mac_address)
        if not encontrado:
            raise PortalOpenDispositivoNoExiste(
                f'No se encontró el MAC {televisor.mac_address} en el portal.'
            )

        avisar(40, 'Enviando bloqueo…')
        cliente.bloquear([televisor.mac_address], bloquear=bool(televisor.inhabilitado))
        res.paso('Lock Status: ' + ('Lock' if televisor.inhabilitado else 'Unlock'))

        if sincronizar_fecha:
            avisar(70, 'Actualizando fecha…')
            _empujar_fecha(cliente, encontrado['id'], televisor, res)

        avisar(90, 'Verificando…')
        _leer_estado(cliente, encontrado['id'], res)

        res.ok = True
        res.aplicado = True
        return res, False
    except PortalOpenError as e:
        res.ok = False
        res.error = str(e)
        res.paso(f'ERROR: {res.error}')
        return res, merece_respaldo(e)
    except Exception as e:  # noqa: BLE001
        # Un fallo inesperado (red, bug propio) también merece el respaldo.
        res.ok = False
        res.error = f'{type(e).__name__}: {e}'
        res.paso(f'ERROR: {res.error}')
        return res, True


def sincronizar_estado(
    televisor, sincronizar_fecha=True, headless=None, progreso=None
) -> ResultadoSync:
    """Aplica el estado de UN televisor. Misma firma que la versión Selenium.

    `headless` se acepta y se ignora: aquí no hay navegador. Está para que
    ambas implementaciones sean intercambiables sin tocar a quien las llama.
    """
    res, _ = intentar(televisor, sincronizar_fecha, progreso)
    return res


def _empujar_fecha(cliente, device_id, televisor, res):
    """Best-effort: si falla, el bloqueo ya se aplicó y eso es lo que importa."""
    try:
        fecha = _fecha(televisor)
        cliente.actualizar(device_id, next_installment_date=fecha)
        res.paso(f'Next Installment Date: {fecha}')
    except PortalOpenError as e:
        res.paso(f'No se pudo fijar la fecha ({e}). El bloqueo sí se aplicó.')


def _leer_estado(cliente, device_id, res):
    """Relee el estado para dejar constancia de lo que quedó en el portal."""
    try:
        res.remoto_inhabilitado = cliente.detalle(device_id)['status'] == 1
        res.paso(
            'Estado en el portal: '
            + ('Inhabilitado' if res.remoto_inhabilitado else 'Habilitado')
        )
    except PortalOpenError:
        pass  # la verificación es informativa; no invalida la operación


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------

def mapa_macs(cliente=None) -> dict:
    """{MAC en mayúsculas: id} de todos los dispositivos de la marca.

    Se pide una sola vez por lote: resolver el `id` televisor por televisor
    costaría una llamada extra por equipo.
    """
    cliente = cliente or PortalOpenClient()
    mapa = {}
    pagina = 1
    while True:
        items, total = cliente.listar_dispositivos(page_num=pagina, page_size=200)
        for d in items:
            if d['mac']:
                mapa[d['mac'].upper()] = d['id']
        if len(mapa) >= total or not items:
            break
        pagina += 1
    return mapa


def aplicar_lote(televisores, sincronizar_fecha=True):
    """Bloquea/desbloquea un lote entero.

    Devuelve `({televisor_id: ResultadoSync}, [televisores para respaldo])`.
    El bloqueo va en dos llamadas (una por estado deseado, troceadas de 1000);
    la fecha, si se pide, necesita una llamada por televisor.
    """
    cliente = PortalOpenClient()
    resultados = {tv.pk: ResultadoSync() for tv in televisores}
    respaldo = []

    # El mapa se pide siempre, no solo para la fecha: `batch-lock` acepta MACs
    # desconocidas y responde `true` sin hacer nada, así que hay que apartarlas
    # antes o se reportarían como bloqueadas.
    try:
        mapa = mapa_macs(cliente)
    except Exception as e:  # noqa: BLE001
        # Sin mapa no se puede distinguir nada: al respaldo con todo el lote.
        for tv in televisores:
            r = resultados[tv.pk]
            r.ok = False
            r.error = str(e)
        return resultados, list(televisores)

    conocidos = []
    for tv in televisores:
        if tv.mac_address.upper() in mapa:
            conocidos.append(tv)
        else:
            r = resultados[tv.pk]
            r.ok = False
            r.error = f'No se encontró el MAC {tv.mac_address} en el portal.'

    for inhabilitar in (True, False):
        grupo = [tv for tv in conocidos if bool(tv.inhabilitado) is inhabilitar]
        if not grupo:
            continue
        for i in range(0, len(grupo), MAX_LOTE):
            trozo = grupo[i:i + MAX_LOTE]
            macs = [tv.mac_address for tv in trozo]
            try:
                cliente.bloquear(macs, bloquear=inhabilitar)
                for tv in trozo:
                    r = resultados[tv.pk]
                    r.ok = True
                    r.aplicado = True
                    r.remoto_inhabilitado = inhabilitar
                    r.paso('Lock Status: ' + ('Lock' if inhabilitar else 'Unlock'))
            except Exception as e:  # noqa: BLE001
                reintentable = (
                    merece_respaldo(e) if isinstance(e, PortalOpenError) else True
                )
                for tv in trozo:
                    r = resultados[tv.pk]
                    r.ok = False
                    r.error = str(e)
                    if reintentable:
                        respaldo.append(tv)

    if sincronizar_fecha:
        for tv in conocidos:
            r = resultados[tv.pk]
            if not r.aplicado:
                continue
            _empujar_fecha(cliente, mapa[tv.mac_address.upper()], tv, r)

    return resultados, respaldo
