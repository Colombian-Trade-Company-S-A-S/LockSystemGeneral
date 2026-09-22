// Identificador de este navegador, para la sesión única.
//
// El backend permite una sola sesión por cuenta y una sola cuenta por
// navegador; para distinguir "desde dónde" habla cada cliente, mandamos este
// identificador en la cabecera `X-Device-Id` de todas las peticiones.
//
// Vive en localStorage y NO se borra al cerrar sesión: el navegador sigue
// siendo el mismo aunque cambie quien lo usa. Eso es justo lo que permite que,
// tras un logout limpio, otra persona pueda entrar en ese mismo equipo.
//
// Que lo guarde el navegador significa que se puede borrar a mano y pedir uno
// nuevo. Eso no da dos sesiones a la vez —la cuenta solo apunta a un
// dispositivo y el backend rechaza el resto—, pero sí es el motivo por el que
// la regla "un navegador, una cuenta" es una barrera de uso y no de seguridad.

import { config } from '@/lib/config'

/** Genera un identificador nuevo. `randomUUID` necesita HTTPS o localhost. */
function nuevoId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  // Reserva para navegadores viejos o contextos sin `crypto.randomUUID`.
  return `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/** Identificador de este navegador, creándolo la primera vez. */
export function getDeviceId(): string {
  const guardado = localStorage.getItem(config.storage.deviceId)
  if (guardado) return guardado

  const generado = nuevoId()
  localStorage.setItem(config.storage.deviceId, generado)
  return generado
}

/**
 * Adopta el identificador que devuelve el backend al iniciar sesión.
 * Normalmente es el mismo que mandamos; si el backend lo rechazó por tener mal
 * formato, nos quedamos con el suyo para no discutir con el servidor.
 */
export function setDeviceId(id: string | undefined): void {
  if (id) localStorage.setItem(config.storage.deviceId, id)
}
