"""Ejecución en segundo plano de la sincronización de un televisor con el portal.

Al guardar el estado de un TV se crea un SyncJob y se lanza un hilo daemon que
aplica el cambio en el portal y va actualizando el porcentaje del job. El
frontend consulta el progreso por polling (igual que whaletv).

El camino normal es la Portal API (`portal/open_sync.py`): ~3 s y sin navegador.
Selenium queda de red de seguridad para cuando la API no esté disponible.
"""
from __future__ import annotations

import threading

from django.db import connections
from django.utils import timezone

from .models import SyncJob, Televisor
from .portal import open_sync
from .portal.selenium_sync import sincronizar_estado as sincronizar_con_selenium
from .sync_limits import cupo_navegador


def _con_selenium(tv, progreso, marcar_corriendo):
    """Selenium abre un navegador: hay que esperar cupo (protege la RAM).

    Mientras espera, el job sigue PENDIENTE ("en cola") y solo se marca
    CORRIENDO al conseguirlo; por eso el estado se fija aquí dentro. Cuando se
    llega como respaldo el job ya estaba CORRIENDO: volver a marcarlo reinicia
    el porcentaje, que es justo lo que pasa (el intento empieza de cero).
    """
    with cupo_navegador(masivo=False):
        marcar_corriendo()
        return sincronizar_con_selenium(tv, progreso=progreso)


def _sincronizar(tv, progreso, marcar_corriendo):
    """Aplica el estado: primero por API, y si falla el SERVICIO, por Selenium.

    No se reintenta cuando el fallo es del DATO (una MAC que el portal no tiene,
    parámetros inválidos): Selenium daría el mismo error 15 s más tarde.

    Ojo con el orden y el cupo: `cupo_navegador` es un semáforo de navegadores
    (SYNC_MAX_MANUAL=1 por defecto). Tomarlo antes de intentar la API pondría a
    los televisores en fila de uno en uno para algo que no usa navegador, que es
    justo la lentitud que veníamos a quitar. Por eso solo se pide en el respaldo.
    """
    if not open_sync.usa_open():
        return _con_selenium(tv, progreso, marcar_corriendo)

    marcar_corriendo()
    res, usar_respaldo = open_sync.intentar(tv, progreso=progreso)
    if res.ok or not usar_respaldo:
        return res

    fallo_api = res.error
    res = _con_selenium(tv, progreso, marcar_corriendo)
    res.log.insert(0, f'La API falló ({fallo_api}). Se reintentó con Selenium.')
    if not res.ok and res.error:
        res.error = f'API: {fallo_api} | Selenium: {res.error}'
    return res


def _ejecutar(job_id: int):
    try:
        job = SyncJob.objects.get(pk=job_id)
        tv = Televisor.objects.get(pk=job.televisor_id)

        def progreso(pct, _msg=''):
            SyncJob.objects.filter(pk=job_id).update(porcentaje=pct)

        def marcar_corriendo():
            SyncJob.objects.filter(pk=job_id).update(
                estado=SyncJob.CORRIENDO, porcentaje=5
            )

        res = _sincronizar(tv, progreso, marcar_corriendo)

        if res.ok and res.aplicado:
            SyncJob.objects.filter(pk=job_id).update(
                estado=SyncJob.TERMINADO,
                porcentaje=100,
                terminado_en=timezone.now(),
            )
        else:
            SyncJob.objects.filter(pk=job_id).update(
                estado=SyncJob.ERROR,
                porcentaje=100,
                error=(res.error or 'No se pudo aplicar el cambio en el portal.')[:1000],
                terminado_en=timezone.now(),
            )
    except Exception as e:  # noqa: BLE001
        SyncJob.objects.filter(pk=job_id).update(
            estado=SyncJob.ERROR,
            porcentaje=100,
            error=f'{type(e).__name__}: {e}'[:1000],
            terminado_en=timezone.now(),
        )
    finally:
        # El hilo tiene su propia conexión a la BD; hay que cerrarla.
        connections.close_all()


def lanzar_sync_job(
    televisor: Televisor, inhabilitar: bool, usuario=None, ip: str | None = None
) -> SyncJob:
    """Crea el SyncJob y lanza el hilo que lo procesa. Devuelve el job."""
    job = SyncJob.objects.create(
        # La empresa sale del televisor, que ya viene acotado por la vista: el
        # hilo de fondo no tiene request, así que no puede deducirla él.
        empresa=televisor.empresa,
        televisor=televisor,
        inhabilitar=inhabilitar,
        usuario=usuario if usuario and usuario.is_authenticated else None,
        ip=ip,
    )
    hilo = threading.Thread(target=_ejecutar, args=(job.pk,), daemon=True)
    hilo.start()
    return job
