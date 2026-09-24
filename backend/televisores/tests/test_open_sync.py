"""Decisión entre la Portal API y Selenium.

No tocan la red ni la base: lo que se prueba es *a quién se llama y cuándo*,
que es donde estuvo el riesgo al portar. Las llamadas reales al portal se
verificaron a mano contra ACC.
"""
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from televisores import sync_runner
from televisores.models import Televisor
from televisores.portal import open_sync
from televisores.portal.open_client import (
    PortalOpenBrandNoAutorizado,
    PortalOpenDispositivoNoExiste,
    PortalOpenError,
    PortalOpenMacInvalida,
)
from televisores.portal.selenium_sync import ResultadoSync

CFG = {
    'ENABLED': True,
    'HOST': 'acc-lockservice.whaletv.com',
    'ACCESS_KEY': 'ak',
    'SECRET_KEY': 'sk',
    'BRAND_ID': '123',
    'API_BASE': '/lock-portal/open/v1',
    'TIMEOUT': 5,
}


def _cfg(**cambios):
    return {**CFG, **cambios}


class UsaOpenTests(SimpleTestCase):
    """Con la configuración a medias es preferible Selenium a fallar siempre."""

    def test_activo_con_las_tres_credenciales(self):
        with override_settings(WHALETV_LOCK_PORTAL_API=CFG):
            self.assertTrue(open_sync.usa_open())

    def test_el_interruptor_manda(self):
        with override_settings(WHALETV_LOCK_PORTAL_API=_cfg(ENABLED=False)):
            self.assertFalse(open_sync.usa_open())

    def test_sin_brand_id_no_se_activa(self):
        with override_settings(WHALETV_LOCK_PORTAL_API=_cfg(BRAND_ID='')):
            self.assertFalse(open_sync.usa_open())

    def test_sin_secreto_no_se_activa(self):
        with override_settings(WHALETV_LOCK_PORTAL_API=_cfg(SECRET_KEY='')):
            self.assertFalse(open_sync.usa_open())


class MereceRespaldoTests(SimpleTestCase):
    """Un fallo del DATO daría el mismo error por Selenium 15 s más tarde."""

    def test_los_fallos_de_dato_no_van_a_selenium(self):
        for exc in (
            PortalOpenDispositivoNoExiste('no está'),
            PortalOpenMacInvalida('MAC mala'),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(open_sync.merece_respaldo(exc))

    def test_los_fallos_de_servicio_si_van_a_selenium(self):
        # El brand desvinculado entra aquí a propósito: Zeasn lo ha perdido
        # varias veces y Selenium sigue funcionando mientras tanto.
        for exc in (
            PortalOpenError('503'),
            PortalOpenBrandNoAutorizado('no permission for this brand'),
            ConnectionResetError(),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertTrue(open_sync.merece_respaldo(exc))


class MismoEntornoTests(SimpleTestCase):
    """La API y Selenium se configuran por separado y pueden descasarse.

    Pasó de verdad: la API quedó apuntando a ACC mientras Selenium entraba a
    producción, y los televisores reales (que solo están en producción) se
    reportaban como inexistentes sin llegar a intentarse por Selenium.
    """

    PORTAL_PROD = {'LOGIN_URL': 'https://lockservice.whaletv.com/login',
                   'DIAS_DESFASE': 30}

    def test_detecta_entornos_distintos(self):
        with override_settings(
            WHALETV_LOCK_PORTAL_API=_cfg(HOST='acc-lockservice.whaletv.com'),
            WHALETV_PORTAL=self.PORTAL_PROD,
        ):
            self.assertFalse(open_sync.mismo_entorno())

    def test_detecta_el_mismo_entorno(self):
        with override_settings(
            WHALETV_LOCK_PORTAL_API=_cfg(HOST='lockservice.whaletv.com'),
            WHALETV_PORTAL=self.PORTAL_PROD,
        ):
            self.assertTrue(open_sync.mismo_entorno())

    def test_mac_desconocido_va_a_selenium_si_los_entornos_diferen(self):
        exc = PortalOpenDispositivoNoExiste('no está')
        with override_settings(
            WHALETV_LOCK_PORTAL_API=_cfg(HOST='acc-lockservice.whaletv.com'),
            WHALETV_PORTAL=self.PORTAL_PROD,
        ):
            self.assertTrue(open_sync.merece_respaldo(exc))

    def test_mac_desconocido_es_definitivo_si_es_el_mismo_portal(self):
        exc = PortalOpenDispositivoNoExiste('no está')
        with override_settings(
            WHALETV_LOCK_PORTAL_API=_cfg(HOST='lockservice.whaletv.com'),
            WHALETV_PORTAL=self.PORTAL_PROD,
        ):
            self.assertFalse(open_sync.merece_respaldo(exc))


class FormatoFechaTests(SimpleTestCase):
    """MM/dd/yyyy con BARRAS. El ejemplo del PDF trae guiones y la API los
    rechaza; verificado contra ACC."""

    def test_formato(self):
        tv = Televisor(mac_address='AA:BB:CC:DD:EE:01', inhabilitado=True)
        fecha = open_sync._fecha(tv)
        self.assertRegex(fecha, r'^\d{2}/\d{2}/\d{4}$')

    def test_inhabilitado_manda_fecha_pasada(self):
        """El cron de Zeasn lee esta fecha: tiene que estar de acuerdo con el
        bloqueo que se acaba de aplicar, no en contra."""
        from django.utils import timezone

        hoy = timezone.localdate()
        self.assertLess(
            Televisor(mac_address='A', inhabilitado=True).fecha_sincronizar, hoy
        )
        self.assertGreater(
            Televisor(mac_address='A', inhabilitado=False).fecha_sincronizar, hoy
        )


class FallbackTests(SimpleTestCase):
    """`_sincronizar` decide entre API y Selenium."""

    def setUp(self):
        self.tv = Televisor(mac_address='AA:BB:CC:DD:EE:01', inhabilitado=True)
        self.corriendo = 0

    def _marcar(self):
        self.corriendo += 1

    @staticmethod
    def _selenium_ok():
        res = ResultadoSync()
        res.ok = True
        res.aplicado = True
        return res

    def test_api_ok_no_abre_navegador(self):
        ok = self._selenium_ok()
        with override_settings(WHALETV_LOCK_PORTAL_API=CFG), \
                mock.patch.object(open_sync, 'intentar', return_value=(ok, False)), \
                mock.patch.object(sync_runner, '_con_selenium') as sel:
            res = sync_runner._sincronizar(self.tv, None, self._marcar)
        self.assertTrue(res.ok)
        sel.assert_not_called()
        self.assertEqual(self.corriendo, 1, 'el job debe marcarse CORRIENDO')

    def test_fallo_de_servicio_cae_a_selenium(self):
        malo = ResultadoSync()
        malo.ok = False
        malo.error = 'HTTP 503'
        with override_settings(WHALETV_LOCK_PORTAL_API=CFG), \
                mock.patch.object(open_sync, 'intentar', return_value=(malo, True)), \
                mock.patch.object(
                    sync_runner, '_con_selenium', return_value=self._selenium_ok()
                ) as sel:
            res = sync_runner._sincronizar(self.tv, None, self._marcar)
        sel.assert_called_once()
        self.assertTrue(res.ok)
        self.assertIn('La API falló', res.log[0])

    def test_fallo_de_dato_no_cae_a_selenium(self):
        malo = ResultadoSync()
        malo.ok = False
        malo.error = 'No se encontró el MAC'
        with override_settings(WHALETV_LOCK_PORTAL_API=CFG), \
                mock.patch.object(open_sync, 'intentar', return_value=(malo, False)), \
                mock.patch.object(sync_runner, '_con_selenium') as sel:
            res = sync_runner._sincronizar(self.tv, None, self._marcar)
        sel.assert_not_called()
        self.assertFalse(res.ok)

    def test_si_fallan_los_dos_se_ven_ambos_errores(self):
        api = ResultadoSync()
        api.ok = False
        api.error = 'HTTP 503'
        sel_res = ResultadoSync()
        sel_res.ok = False
        sel_res.error = 'no abrió Chrome'
        with override_settings(WHALETV_LOCK_PORTAL_API=CFG), \
                mock.patch.object(open_sync, 'intentar', return_value=(api, True)), \
                mock.patch.object(sync_runner, '_con_selenium', return_value=sel_res):
            res = sync_runner._sincronizar(self.tv, None, self._marcar)
        self.assertIn('HTTP 503', res.error)
        self.assertIn('no abrió Chrome', res.error)

    def test_api_apagada_va_directo_a_selenium(self):
        with override_settings(WHALETV_LOCK_PORTAL_API=_cfg(ENABLED=False)), \
                mock.patch.object(open_sync, 'intentar') as api, \
                mock.patch.object(
                    sync_runner, '_con_selenium', return_value=self._selenium_ok()
                ) as sel:
            sync_runner._sincronizar(self.tv, None, self._marcar)
        api.assert_not_called()
        sel.assert_called_once()
