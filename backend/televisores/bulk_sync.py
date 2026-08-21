"""Ejecución en segundo plano de una sincronización masiva (Enrolar Estado).

Un solo hilo abre UNA sesión del portal (un login) y recorre los televisores del
lote aplicando su estado, actualizando el progreso del BulkSyncJob. El frontend
consulta el avance por polling.
"""
from __future__ import annotations

import threading

from django.db import connections
from django.utils import timezone

from .models import BulkSyncItem, BulkSyncJob, Televisor
from .portal.client import PortalClient, PortalError
from .portal.selenium_sync import abrir_sesion, aplicar_en_sesion
from .sync_limits import cupo_navegador


def _ejecutar(job_id: int):
    """Espera cupo de navegador (masivo) y corre el lote. Mientras espera, el job
    queda PENDIENTE ("en cola"); solo al conseguir cupo se abre el navegador y se
    marca CORRIENDO dentro de `_correr_lote`."""
    with cupo_navegador(masivo=True):
        _correr_lote(job_id)


def _correr_lote(job_id: int):
    driver = None
    try:
        BulkSyncJob.objects.filter(pk=job_id).update(estado=BulkSyncJob.CORRIENDO)
        items = list(
            BulkSyncItem.objects.filter(job_id=job_id).values_list('pk', 'televisor_id')
        )

        driver, wait = abrir_sesion()

        ok = 0
        err = 0
        cancelado = False
        for procesados, (item_pk, tv_id) in enumerate(items, start=1):
            if BulkSyncJob.objects.filter(
                pk=job_id, cancelar_solicitado=True
            ).exists():
                cancelado = True
                break

            try:
                tv = Televisor.objects.get(pk=tv_id)
                res = aplicar_en_sesion(driver, wait, tv)
                if res.ok and res.aplicado:
                    estado_item = BulkSyncItem.OK
                    mensaje = 'Inhabilitado' if tv.inhabilitado else 'Habilitado'
                    ok += 1
                else:
                    estado_item = BulkSyncItem.ERROR
                    mensaje = res.error or 'No se pudo aplicar.'
                    err += 1
            except Exception as e:  # noqa: BLE001
                estado_item = BulkSyncItem.ERROR
                mensaje = f'{type(e).__name__}: {e}'
                err += 1

            BulkSyncItem.objects.filter(pk=item_pk).update(
                estado=estado_item, mensaje=mensaje[:500]
            )
            BulkSyncJob.objects.filter(pk=job_id).update(
                procesados=procesados, ok_count=ok, error_count=err
            )

        BulkSyncJob.objects.filter(pk=job_id).update(
            estado=BulkSyncJob.CANCELADO if cancelado else BulkSyncJob.TERMINADO,
            terminado_en=timezone.now(),
        )
    except Exception as e:  # noqa: BLE001
        # Falló el arranque/login: se marcan los items pendientes como error.
        BulkSyncItem.objects.filter(
            job_id=job_id, estado=BulkSyncItem.PENDIENTE
        ).update(estado=BulkSyncItem.ERROR, mensaje=f'{type(e).__name__}: {e}'[:500])
        BulkSyncJob.objects.filter(pk=job_id).update(
            estado=BulkSyncJob.ERROR,
            error_count=BulkSyncItem.objects.filter(
                job_id=job_id, estado=BulkSyncItem.ERROR
            ).count(),
            terminado_en=timezone.now(),
        )
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass
        connections.close_all()


def _ejecutar_validacion(job_id: int):
    """Validación masiva (dry-run): lee el estado del portal por API y lo compara
    con el estado local. No modifica nada."""
    try:
        BulkSyncJob.objects.filter(pk=job_id).update(estado=BulkSyncJob.CORRIENDO)
        items = list(
            BulkSyncItem.objects.filter(job_id=job_id).values_list('pk', 'televisor_id')
        )
        client = PortalClient()

        ok = 0
        err = 0
        cancelado = False
        for procesados, (item_pk, tv_id) in enumerate(items, start=1):
            if BulkSyncJob.objects.filter(
                pk=job_id, cancelar_solicitado=True
            ).exists():
                cancelado = True
                break

            try:
                tv = Televisor.objects.get(pk=tv_id)
                data = client.get_status(tv.eui64_portal)
                remoto = data['lockStatus'] == 1
                local = bool(tv.inhabilitado)
                coincide = remoto == local
                estado_item = BulkSyncItem.OK if coincide else BulkSyncItem.ERROR
                txt = 'Inhabilitado' if remoto else 'Habilitado'
                txt_local = 'Inhabilitado' if local else 'Habilitado'
                mensaje = (
                    f'Coinciden ({txt}).'
                    if coincide
                    else f'Portal: {txt} | App: {txt_local}. Conviene sincronizar.'
                )
                BulkSyncItem.objects.filter(pk=item_pk).update(
                    estado=estado_item,
                    mensaje=mensaje,
                    remoto_inhabilitado=remoto,
                    local_inhabilitado=local,
                    coincide=coincide,
                )
                ok += 1 if coincide else 0
                err += 0 if coincide else 1
            except PortalError as e:
                BulkSyncItem.objects.filter(pk=item_pk).update(
                    estado=BulkSyncItem.ERROR, mensaje=str(e)[:500]
                )
                err += 1
            except Exception as e:  # noqa: BLE001
                BulkSyncItem.objects.filter(pk=item_pk).update(
                    estado=BulkSyncItem.ERROR, mensaje=f'{type(e).__name__}: {e}'[:500]
                )
                err += 1

            BulkSyncJob.objects.filter(pk=job_id).update(
                procesados=procesados, ok_count=ok, error_count=err
            )

        BulkSyncJob.objects.filter(pk=job_id).update(
            estado=BulkSyncJob.CANCELADO if cancelado else BulkSyncJob.TERMINADO,
            terminado_en=timezone.now(),
        )
    except Exception as e:  # noqa: BLE001
        BulkSyncJob.objects.filter(pk=job_id).update(
            estado=BulkSyncJob.ERROR, terminado_en=timezone.now()
        )
        print(f'[validacion] error: {e}')
    finally:
        connections.close_all()


def lanzar_validacion_masiva(
    empresa, televisores=None, errores_import=None
) -> BulkSyncJob | None:
    """Crea un BulkSyncJob de validación y lanza el hilo.

    Sin `televisores` valida TODOS los de la empresa (lo que usa el panel). La
    integración pasa una lista ya filtrada por serial (ver
    `lanzar_validacion_por_serial`). `errores_import` deja constancia, en el job,
    de los seriales pedidos que no existían (el frontend/ERP los ve por polling).
    """
    if televisores is None:
        televisores = list(Televisor.objects.filter(empresa=empresa))
    if not televisores:
        return None
    job = BulkSyncJob.objects.create(
        empresa=empresa,
        modo=BulkSyncJob.VALIDACION,
        total=len(televisores),
        errores_import=errores_import or [],
    )
    BulkSyncItem.objects.bulk_create([
        BulkSyncItem(
            job=job,
            televisor=tv,
            mac_address=tv.mac_address,
            inhabilitar=tv.inhabilitado,
        )
        for tv in televisores
    ])
    threading.Thread(target=_ejecutar_validacion, args=(job.pk,), daemon=True).start()
    return job


def lanzar_validacion_por_serial(registros, empresa) -> BulkSyncJob | None:
    """Validación masiva para INTEGRACIÓN: valida SOLO los seriales del JSON.

    `registros` es una lista de objetos `{"serial_number": ...}` (el mismo
    formato que el cambio de estado masivo). Resuelve cada serial dentro de la
    empresa —insensible a mayúsculas— y valida únicamente esos televisores. Los
    seriales que no existan quedan reportados en el `errores_import` del job.

    Devuelve None si ningún serial válido resolvió (no hay nada que validar).
    """
    from django.db.models.functions import Upper

    if not isinstance(registros, list) or not registros:
        return None

    errores: list[str] = []
    orden: list[str] = []          # seriales (mayúsculas) en orden de aparición
    original: dict[str, str] = {}  # serial_upper -> serial tal cual lo mandaron
    for i, reg in enumerate(registros, start=1):
        if not isinstance(reg, dict):
            errores.append(f'Registro {i}: debe ser un objeto.')
            continue
        serial = str(reg.get('serial_number') or reg.get('serial') or '').strip()
        if not serial:
            errores.append(f'Registro {i}: falta "serial_number".')
            continue
        clave = serial.upper()
        if clave not in original:
            orden.append(clave)
            original[clave] = serial

    # Igual que en el cambio de estado por serial: se compara Upper(serial) para
    # que 'sn-1' encuentre 'SN-1' (el `__in` a secas sería sensible a mayúsculas).
    por_serial = {
        tv.serial_number.upper(): tv
        for tv in Televisor.objects.annotate(_su=Upper('serial_number')).filter(
            empresa=empresa, _su__in=orden,
        )
    }

    televisores: list[Televisor] = []
    for clave in orden:
        tv = por_serial.get(clave)
        if tv is None:
            errores.append(
                f'{original[clave]}: no existe un televisor con ese serial en tu '
                'empresa.'
            )
        else:
            televisores.append(tv)

    return lanzar_validacion_masiva(
        empresa, televisores=televisores, errores_import=errores
    )


def lanzar_bulk_job(
    cambiados: list[Televisor],
    resumen: dict,
    empresa,
    usuario=None,
    ip: str | None = None,
) -> BulkSyncJob:
    """Crea el BulkSyncJob + items para los TV cambiados y lanza el hilo."""
    job = BulkSyncJob.objects.create(
        empresa=empresa,
        total=len(cambiados),
        creados=resumen.get('creados', 0),
        actualizados=resumen.get('actualizados', 0),
        errores_import=resumen.get('errores', []),
        usuario=usuario if usuario and usuario.is_authenticated else None,
        ip=ip,
    )
    BulkSyncItem.objects.bulk_create([
        BulkSyncItem(
            job=job,
            televisor=tv,
            mac_address=tv.mac_address,
            inhabilitar=tv.inhabilitado,
        )
        for tv in cambiados
    ])
    hilo = threading.Thread(target=_ejecutar, args=(job.pk,), daemon=True)
    hilo.start()
    return job
