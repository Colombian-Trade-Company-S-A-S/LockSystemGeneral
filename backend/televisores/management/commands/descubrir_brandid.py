"""Averigua el brandId numérico de la marca (RCA) espiando el portal web.

La Portal API exige `brandId` en todos sus endpoints, pero la doc no dice cómo
obtenerlo. El portal web sí lo sabe: el desplegable "Brand" de Lock Screen
Configuration se llena con una llamada XHR que trae los ids. Como el portal es
Element UI (Vue), el id no está en el DOM — hay que leer la respuesta de red.

    manage.py descubrir_brandid
    manage.py descubrir_brandid --ver   # con navegador visible

Solo navega y lee. No toca nada del portal.
"""
from __future__ import annotations

import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from televisores.portal.selenium_sync import (
    ResultadoSync,
    _build_service,
    _login,
)

# Páginas que suelen cargar la lista de marcas.
RUTAS = ['/deviceManage/deviceList', '/lockScreen/config', '/deviceManage/lockScreen']

# Se ejecuta en cada documento antes que el código del portal. Guarda cada
# respuesta que mencione una marca en sessionStorage, bajo `__cap`.
_INTERCEPTOR = r"""
(function () {
  var CLAVE = '__cap';
  function guardar(url, texto) {
    try {
      if (!texto || !/brand|RCA/i.test(texto)) return;
      var previo = JSON.parse(sessionStorage.getItem(CLAVE) || '[]');
      previo.push({ url: url, body: String(texto).slice(0, 4000) });
      sessionStorage.setItem(CLAVE, JSON.stringify(previo.slice(-40)));
    } catch (e) {}
  }

  var fetchOriginal = window.fetch;
  if (fetchOriginal) {
    window.fetch = function () {
      var url = arguments[0] && arguments[0].url ? arguments[0].url : arguments[0];
      return fetchOriginal.apply(this, arguments).then(function (resp) {
        try { resp.clone().text().then(function (t) { guardar(url, t); }); } catch (e) {}
        return resp;
      });
    };
  }

  var abrirOriginal = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (metodo, url) {
    this.addEventListener('load', function () {
      try { guardar(url, this.responseText); } catch (e) {}
    });
    return abrirOriginal.apply(this, arguments);
  };
})();
"""


class Command(BaseCommand):
    help = 'Descubre el brandId leyendo el tráfico XHR del portal web.'

    def add_arguments(self, parser):
        parser.add_argument('--ver', action='store_true', help='Navegador visible.')

    def handle(self, *args, **opts):
        from selenium import webdriver

        cfg = settings.WHALETV_PORTAL
        if not cfg['EMAIL'] or not cfg['PASSWORD']:
            self.stderr.write(self.style.ERROR('Faltan WHALETV_PORTAL_EMAIL/_PASSWORD.'))
            return

        options = Options()
        if not opts['ver']:
            options.add_argument('--headless=new')
        for arg in ('--no-sandbox', '--disable-dev-shm-usage', '--window-size=1400,900'):
            options.add_argument(arg)
        # Registrar el tráfico de red para poder releer los cuerpos por CDP.
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        driver = webdriver.Chrome(service=_build_service(), options=options)
        try:
            # Chrome descarta los cuerpos de red al navegar, así que leerlos al
            # final no sirve. En vez de eso, se parchean fetch/XHR ANTES de que
            # cargue cada documento y se acumulan las respuestas en
            # sessionStorage, que sí sobrevive a la navegación del mismo origen.
            driver.execute_cdp_cmd(
                'Page.addScriptToEvaluateOnNewDocument', {'source': _INTERCEPTOR}
            )

            wait = WebDriverWait(driver, cfg.get('TIMEOUT', 30))
            _login(driver, wait, cfg, ResultadoSync())

            base = cfg['LOGIN_URL'].rsplit('/', 1)[0]
            for ruta in RUTAS:
                self.stdout.write(f'Visitando {ruta}…')
                try:
                    driver.get(base + ruta)
                    WebDriverWait(driver, 10).until(
                        lambda d: d.execute_script('return document.readyState') == 'complete'
                    )
                except Exception as e:  # noqa: BLE001 — una ruta que no existe no aborta
                    self.stdout.write(self.style.WARNING(f'  {type(e).__name__}'))

            self._volcar_almacenamiento(driver)
            self._volcar_red(driver)
        finally:
            driver.quit()

    # -- fuentes donde puede estar el id --------------------------------
    def _volcar_almacenamiento(self, driver):
        self.stdout.write('\n=== localStorage / sessionStorage ===')
        for almacen in ('localStorage', 'sessionStorage'):
            try:
                datos = driver.execute_script(
                    f'return JSON.stringify(window.{almacen});'
                )
            except Exception:  # noqa: BLE001
                continue
            for trozo in self._trozos_interesantes(datos or ''):
                self.stdout.write(f'  [{almacen}] {trozo}')

    def _volcar_red(self, driver):
        self.stdout.write('\n=== respuestas XHR con "brand" ===')
        try:
            crudo = driver.execute_script("return sessionStorage.getItem('__cap');")
            capturas = json.loads(crudo or '[]')
        except Exception:  # noqa: BLE001
            capturas = []

        if not capturas:
            self.stdout.write(self.style.WARNING(
                '  Ninguna respuesta mencionó una marca. Prueba con --ver para '
                'navegar a mano hasta Lock Screen Configuration.'
            ))
            return

        for captura in capturas:
            trozos = self._trozos_interesantes(captura.get('body', ''))
            if trozos:
                self.stdout.write(f"\n  {captura.get('url')}")
                for trozo in trozos:
                    self.stdout.write(f'    {trozo}')

    @staticmethod
    def _trozos_interesantes(texto: str) -> list[str]:
        """Fragmentos que mencionan una marca junto a un id numérico."""
        if not texto or not re.search(r'brand|RCA', texto, re.I):
            return []
        encontrados = []
        # {"brandId":123,...} / {"id":123,"name":"RCA"} / "brandName":"RCA"
        for patron in (
            r'.{0,80}brandId["\']?\s*[:=]\s*["\']?\d+.{0,40}',
            r'.{0,80}["\']RCA["\'].{0,80}',
            r'.{0,60}brandName.{0,80}',
        ):
            encontrados += re.findall(patron, texto, re.I)
        # Sin duplicados, conservando el orden.
        return list(dict.fromkeys(t.strip() for t in encontrados))[:15]
