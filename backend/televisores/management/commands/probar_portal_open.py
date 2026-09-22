"""Prueba la Device Lock Portal API y descubre el brandId por tanteo.

    manage.py probar_portal_open                      # ¿sirven las llaves?
    manage.py probar_portal_open --descubrir-brand    # ¿cuál es el brandId?
    manage.py probar_portal_open --mac 3C:BE:8E:BD:EF:01

Solo hace lecturas (GET /devices). No bloquea, no importa, no limpia nada.
"""
from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from televisores.portal.open_client import (
    PortalOpenAuthError,
    PortalOpenBrandNoAutorizado,
    PortalOpenClient,
    PortalOpenError,
)

# Hosts a tantear cuando no sabemos en qué entorno viven las llaves.
HOSTS = ['acc-lockservice.whaletv.com', 'lockservice.whaletv.com']


class Command(BaseCommand):
    help = 'Diagnostica la Device Lock Portal API (auth, brandId, un dispositivo).'

    def add_arguments(self, parser):
        parser.add_argument('--host', help='Fuerza un host en vez de tantear los dos.')
        parser.add_argument('--descubrir-brand', action='store_true',
                            help='Tantea brandIds hasta que uno responda.')
        parser.add_argument('--brand', help='Usa este brandId en vez del de settings.')
        parser.add_argument('--desde', type=int, default=1)
        parser.add_argument('--hasta', type=int, default=300,
                            help='Rango de brandIds a tantear (por defecto 1-300).')
        parser.add_argument('--mac', help='Consulta esta MAC una vez resuelto el brand.')

    def handle(self, *args, **opts):
        cfg = dict(settings.WHALETV_LOCK_PORTAL_API)
        if not cfg['ACCESS_KEY'] or not cfg['SECRET_KEY']:
            self.stderr.write(self.style.ERROR(
                'Faltan WHALETV_LOCK_PORTAL_API_ACCESS_KEY/_SECRET_KEY en el entorno.'
            ))
            return

        self.stdout.write(f"accessKey: {cfg['ACCESS_KEY'][:8]}…{cfg['ACCESS_KEY'][-4:]}")

        hosts = [opts['host']] if opts['host'] else HOSTS
        host_ok = None
        for host in hosts:
            cfg['HOST'] = host
            self.stdout.write(f'\n--- {host} ---')
            estado = self._probar_auth(PortalOpenClient(cfg))
            if estado == 'ok':
                host_ok = host
                break

        if not host_ok:
            self.stderr.write(self.style.ERROR(
                '\nLas llaves no sirven en ninguno de los hosts probados.'
            ))
            return

        cfg['HOST'] = host_ok
        cliente = PortalOpenClient(cfg)

        brand = opts['brand'] or cfg.get('BRAND_ID')
        if opts['descubrir_brand'] or not brand:
            brand = self._descubrir_brand(cliente, opts['desde'], opts['hasta'])
            if not brand:
                return
            self.stdout.write(self.style.SUCCESS(
                f'\n>>> Pon esto en .env y en Render:\n'
                f'    WHALETV_LOCK_PORTAL_API_HOST={host_ok}\n'
                f'    WHALETV_LOCK_PORTAL_API_BRAND_ID={brand}'
            ))

        self._resumen(cliente, brand, opts.get('mac'))

    # -- pasos ----------------------------------------------------------
    def _probar_auth(self, cliente) -> str:
        """Distingue 'llaves malas' de 'llaves buenas, brandId malo'."""
        try:
            cliente.listar_dispositivos(brand_id=1, page_size=1)
        except PortalOpenBrandNoAutorizado:
            # 270202 = firma válida, solo que el brandId 1 no es nuestro.
            self.stdout.write(self.style.SUCCESS(
                'Autenticación OK (270202: el brandId 1 no es nuestro, es lo esperado).'
            ))
            return 'ok'
        except PortalOpenAuthError as e:
            self.stdout.write(self.style.ERROR(f'401 — {e}'))
            return 'auth'
        except PortalOpenError as e:
            self.stdout.write(self.style.WARNING(f'Respuesta inesperada: {e}'))
            return 'raro'
        self.stdout.write(self.style.SUCCESS(
            'Autenticación OK y además el brandId 1 responde.'
        ))
        return 'ok'

    def _descubrir_brand(self, cliente, desde: int, hasta: int):
        self.stdout.write(f'\nTanteando brandId {desde}-{hasta}…')
        for brand in range(desde, hasta + 1):
            try:
                items, total = cliente.listar_dispositivos(brand_id=brand, page_size=1)
            except PortalOpenBrandNoAutorizado:
                continue
            except PortalOpenError as e:
                self.stdout.write(self.style.WARNING(f'  brandId {brand}: {e}'))
                continue
            self.stdout.write(self.style.SUCCESS(
                f'  brandId {brand} AUTORIZADO — {total} dispositivos'
            ))
            return brand
        self.stderr.write(self.style.ERROR(
            f'Ningún brandId entre {desde} y {hasta} está autorizado. '
            'Amplía el rango con --hasta, o pídeselo a Zeasn.'
        ))
        return None

    def _resumen(self, cliente, brand, mac):
        try:
            items, total = cliente.listar_dispositivos(brand_id=brand, page_size=5)
        except PortalOpenError as e:
            self.stderr.write(self.style.ERROR(f'Fallo al listar: {e}'))
            return

        self.stdout.write(f'\nDispositivos de la marca {brand}: {total}')
        for d in items:
            self.stdout.write(
                f"  {d['mac'] or d['sn']}  id={d['id']}  "
                f"status={d['status']}  pago={d['paymentStatus']}"
            )

        if not mac:
            return
        self.stdout.write(f'\nBuscando {mac}…')
        try:
            encontrado = cliente.buscar_por_mac(mac, brand_id=brand)
            if not encontrado:
                self.stdout.write(self.style.WARNING('  no está en esta marca'))
                return
            detalle = cliente.detalle(encontrado['id'], brand_id=brand)
            for clave, valor in detalle.items():
                self.stdout.write(f'  {clave}: {valor}')
        except PortalOpenError as e:
            self.stderr.write(self.style.ERROR(f'  {e}'))
