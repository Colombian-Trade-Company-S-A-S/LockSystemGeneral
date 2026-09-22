"""Sesión del portal: cierre por inactividad y sesión única, desde el servidor.

La idea de la inactividad es simple: cada petición autenticada deja una marca de
tiempo en el usuario (`last_activity`). Si el hueco entre esa marca y la
petición actual supera `SESSION_IDLE_TIMEOUT_MINUTES`, el token deja de valer y
hay que volver a iniciar sesión.

Por qué aquí y no solo en el navegador: un temporizador en el frontend es una
comodidad de interfaz, no una barrera. Quien copie un token y lo use desde
`curl` no ejecuta ese temporizador. El plazo tiene que decidirlo y aplicarlo el
backend; el frontend se limita a leerlo (viaja en /api/me/) para sacar al
usuario a la pantalla de login cuando toca.

El plazo se comprueba en los dos sitios por los que entra un token:
la autenticación de cada petición (users/authentication.py) y la renovación del
access token (users/api/views.py). Si solo se comprobara en la primera, bastaría
con refrescar para resucitar una sesión ya muerta.

Sesión única
------------
Una cuenta solo puede estar abierta en un navegador, y un navegador solo puede
tener abierta una cuenta a la vez. Para saber desde dónde entra alguien, cada
cliente manda un identificador de navegador en la cabecera `X-Device-Id`, y la
cuenta se queda apuntando al suyo mientras la sesión viva (`User.device_id`).

Ese identificador lo guarda el navegador, así que alguien decidido puede
borrarlo y presentarse como un navegador nuevo. Lo que eso NO le da son dos
sesiones a la vez: la cuenta solo admite un dispositivo apuntado y, mientras
siga vivo, el login desde cualquier otro se rechaza. La regla "una cuenta, una
sesión" es firme; la de "este navegador ya está ocupado" es la que se apoya en
un dato que manda el cliente.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

# Cada pantalla dispara varias peticiones a la vez; no tiene sentido un UPDATE
# por cada una. Refrescamos la marca como mucho una vez cada tantos segundos.
INTERVALO_DE_ESCRITURA = timedelta(seconds=5)


def plazo_de_inactividad() -> timedelta | None:
    """Plazo configurado, o None si el cierre automático está desactivado."""
    minutos = getattr(settings, 'SESSION_IDLE_TIMEOUT_MINUTES', 0)
    if minutos <= 0:
        return None
    return timedelta(minutes=minutos)


def sesion_expirada(user) -> bool:
    """True si el usuario lleva más del plazo permitido sin dar señales."""
    plazo = plazo_de_inactividad()
    if plazo is None:
        return False

    ultima = getattr(user, 'last_activity', None)
    if ultima is None:
        # Sesión abierta antes de que existiera la marca (o cuenta que aún no
        # ha hecho ninguna petición): no la cerramos por un dato que nunca se
        # escribió. La petición actual ya deja la marca y el reloj arranca ahí.
        return False

    # Sumamos el intervalo de escritura porque la marca puede estar justo esos
    # segundos desactualizada: más vale regalar unos segundos que echar a quien
    # sí estaba trabajando.
    return timezone.now() - ultima > plazo + INTERVALO_DE_ESCRITURA


def registrar_actividad(user, *, forzar: bool = False) -> None:
    """Deja constancia de que el usuario acaba de usar la aplicación.

    `forzar` salta el intervalo de escritura: se usa al iniciar sesión y al
    renovar el token, donde el reloj tiene que quedar puesto en ese instante.
    """
    if plazo_de_inactividad() is None:
        return

    ahora = timezone.now()
    ultima = getattr(user, 'last_activity', None)
    if not forzar and ultima is not None and ahora - ultima < INTERVALO_DE_ESCRITURA:
        return

    # `update()` y no `save()`: escribe una sola columna, no dispara señales y
    # —sobre todo— no toca `updated_at` (auto_now), que audita cambios del
    # perfil y quedaría inservible si se moviera en cada petición.
    get_user_model().objects.filter(pk=user.pk).update(last_activity=ahora)
    user.last_activity = ahora


# ---------------------------------------------------------------------------
# Sesión única (una cuenta = un navegador)
# ---------------------------------------------------------------------------
# Cabecera con la que el cliente dice desde qué navegador habla.
DEVICE_HEADER = 'X-Device-Id'

# Formato aceptado: el frontend lo genera con `crypto.randomUUID()`, pero no nos
# fiamos de lo que llegue. Cualquier cosa fuera de esto se descarta y se cambia
# por un identificador nuevo, así no acaban en la base de datos valores absurdos.
_FORMATO_DISPOSITIVO = re.compile(r'^[A-Za-z0-9._-]{8,64}$')


class SesionEnOtroDispositivo(APIException):
    """La cuenta ya está abierta en otro navegador."""

    status_code = status.HTTP_409_CONFLICT
    default_code = 'sesion_en_otro_dispositivo'
    default_detail = (
        'Esta cuenta ya tiene una sesión abierta en otro dispositivo. '
        'Ciérrala allí o espera a que caduque por inactividad.'
    )


class DispositivoOcupado(APIException):
    """Este navegador ya tiene abierta la sesión de otra cuenta."""

    status_code = status.HTTP_409_CONFLICT
    default_code = 'dispositivo_ocupado'
    default_detail = (
        'Este navegador ya tiene una sesión abierta con otra cuenta. '
        'Cierra esa sesión o entra desde otro navegador.'
    )


def normalizar_dispositivo(valor: str | None) -> str:
    """Devuelve el identificador recibido si es válido, o uno nuevo si no.

    Nunca devuelve vacío: una sesión sin dispositivo sería la forma de saltarse
    la regla entera con solo omitir la cabecera.
    """
    valor = (valor or '').strip()
    if _FORMATO_DISPOSITIVO.match(valor):
        return valor
    return uuid.uuid4().hex


def dispositivo_de(request) -> str:
    """Identificador del navegador que hace la petición (o uno nuevo)."""
    return normalizar_dispositivo(request.headers.get(DEVICE_HEADER))


def describir_dispositivo(request) -> str:
    """Etiqueta legible del navegador, para los mensajes y la pantalla de usuarios."""
    agente = request.headers.get('User-Agent', '')
    # El orden importa: Edge y Opera también dicen "Chrome" en su User-Agent.
    marcas = (('Edg', 'Edge'), ('OPR', 'Opera'), ('Chrome', 'Chrome'),
              ('Firefox', 'Firefox'), ('Safari', 'Safari'))
    navegador = next((nombre for clave, nombre in marcas if clave in agente), '')
    sistema = next(
        (s for s in ('Windows', 'Android', 'iPhone', 'iPad', 'Mac', 'Linux')
         if s in agente),
        '',
    )
    navegador = navegador or 'Navegador desconocido'
    return f'{navegador} en {sistema}' if sistema else navegador


def sesion_activa(user) -> bool:
    """True si la cuenta tiene ahora mismo una sesión ocupando un navegador."""
    return bool(getattr(user, 'device_id', '')) and not sesion_expirada(user)


def dispositivo_ajeno(user, dispositivo: str) -> bool:
    """True si la petición llega desde un navegador distinto al de la sesión.

    Aquí cae un token robado y llevado a otro equipo: sin el identificador del
    navegador que abrió la sesión, no sirve. Si la cuenta no tiene dispositivo
    apuntado (sesión anterior a esta función) no se bloquea nada: el siguiente
    inicio de sesión ya lo dejará puesto.
    """
    actual = getattr(user, 'device_id', '')
    return bool(actual) and actual != (dispositivo or '')


def liberar_sesiones_caducadas(dispositivo: str) -> None:
    """Suelta el navegador que dejaron ocupado sesiones ya vencidas.

    Sin esto, quien cierre el navegador sin desconectarse dejaría ese equipo
    inservible para sus compañeros hasta que alguien lo destrabe a mano.
    """
    modelo = get_user_model()
    for otro in modelo.objects.filter(device_id=dispositivo):
        if sesion_expirada(otro):
            modelo.objects.filter(pk=otro.pk).update(
                device_id='', device_label='', session_started_at=None
            )


def verificar_puede_iniciar_sesion(user, dispositivo: str) -> None:
    """Aplica las dos reglas de sesión única. Lanza un 409 si alguna se incumple.

    Se llama con las credenciales YA validadas: así el mensaje ("hay sesión
    abierta en tal sitio") solo lo ve el dueño de la cuenta y no le sirve a un
    desconocido para averiguar quién está conectado.
    """
    # Antes de nada, suelta lo que en este navegador ya está muerto.
    liberar_sesiones_caducadas(dispositivo)

    # Regla 1 — una cuenta, un navegador. Volver a entrar desde el MISMO
    # navegador sí se permite: es el caso de recargar la página o de haber
    # cerrado la pestaña y volver.
    if sesion_activa(user) and user.device_id != dispositivo:
        detalle = SesionEnOtroDispositivo.default_detail
        if user.device_label:
            detalle = (
                f'Esta cuenta ya tiene una sesión abierta en {user.device_label}. '
                'Ciérrala allí o espera a que caduque por inactividad.'
            )
        raise SesionEnOtroDispositivo(detalle)

    # Regla 2 — un navegador, una cuenta.
    modelo = get_user_model()
    ocupantes = modelo.objects.filter(device_id=dispositivo).exclude(pk=user.pk)
    if any(sesion_activa(otro) for otro in ocupantes):
        raise DispositivoOcupado()


def abrir_sesion(user, dispositivo: str, etiqueta: str) -> None:
    """Apunta la cuenta a este navegador y arranca su reloj de inactividad."""
    ahora = timezone.now()
    get_user_model().objects.filter(pk=user.pk).update(
        device_id=dispositivo,
        device_label=etiqueta,
        session_started_at=ahora,
        last_activity=ahora,
    )
    user.device_id = dispositivo
    user.device_label = etiqueta
    user.session_started_at = ahora
    user.last_activity = ahora
