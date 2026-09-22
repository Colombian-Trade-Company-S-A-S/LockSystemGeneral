// Cierre de sesión por inactividad — parte de interfaz.
//
// El plazo NO se decide aquí: lo fija el backend con
// SESSION_IDLE_TIMEOUT_MINUTES en su `.env` y viaja en el perfil (/api/me/).
// El backend es además quien lo impone de verdad: pasado ese tiempo rechaza los
// tokens. Este hook existe para que el usuario vea la pantalla de login en el
// momento justo, en lugar de quedarse mirando datos de una sesión ya muerta
// hasta que haga clic en algo.
//
// La marca de "última actividad" vive en localStorage y no en una variable del
// módulo: así todas las pestañas abiertas comparten el mismo reloj — usar una
// mantiene viva la sesión en las demás, y cuando vence, vencen todas.

import { useEffect } from 'react'
import { config } from '@/lib/config'

/** Interacciones que cuentan como "el usuario sigue ahí". */
const ACTIVITY_EVENTS = [
  'mousedown',
  'mousemove',
  'keydown',
  'wheel',
  'touchstart',
  'click',
  'scroll',
] as const

/** No escribimos en localStorage en cada `mousemove`: uno por segundo basta. */
const WRITE_THROTTLE_MS = 1_000

/** Cada cuánto comprobamos si venció el plazo. */
const CHECK_INTERVAL_MS = 1_000

/**
 * Cierra la sesión tras `timeoutMinutes` sin actividad del usuario.
 *
 * @param timeoutMinutes Plazo que manda el backend. 0/undefined lo desactiva.
 * @param onExpire Qué hacer al vencer (normalmente el `logout` del contexto).
 */
export function useIdleLogout(
  timeoutMinutes: number | undefined,
  onExpire: () => void,
): void {
  useEffect(() => {
    // Sin sesión iniciada, o con el cierre automático apagado en el backend.
    if (!timeoutMinutes || timeoutMinutes <= 0) return
    const timeoutMs = timeoutMinutes * 60_000

    const key = config.storage.lastActivity

    // El contador arranca ahora: al iniciar sesión o al recargar la página.
    let ultimaEscritura = Date.now()
    localStorage.setItem(key, String(ultimaEscritura))

    const onActividad = () => {
      const ahora = Date.now()
      if (ahora - ultimaEscritura < WRITE_THROTTLE_MS) return
      ultimaEscritura = ahora
      localStorage.setItem(key, String(ahora))
    }

    // Comprobación periódica en lugar de un `setTimeout` largo: el navegador
    // ralentiza los timers en pestañas de fondo y no cuenta el tiempo que el
    // equipo estuvo suspendido. Comparar contra la marca real evita ese sesgo.
    const comprobar = () => {
      const guardado = Number(localStorage.getItem(key))
      const ultima = Number.isFinite(guardado) && guardado > 0 ? guardado : Date.now()
      if (Date.now() - ultima < timeoutMs) return

      localStorage.removeItem(key)
      // Pista para que el login explique por qué se cerró la sesión.
      sessionStorage.setItem(config.storage.logoutReason, 'idle')
      onExpire()
    }

    for (const evento of ACTIVITY_EVENTS) {
      window.addEventListener(evento, onActividad, { passive: true })
    }
    // Al volver a la pestaña, comprueba de inmediato en vez de esperar al
    // siguiente tick (los timers de una pestaña oculta van muy lentos).
    document.addEventListener('visibilitychange', comprobar)
    const intervalo = window.setInterval(comprobar, CHECK_INTERVAL_MS)

    return () => {
      for (const evento of ACTIVITY_EVENTS) {
        window.removeEventListener(evento, onActividad)
      }
      document.removeEventListener('visibilitychange', comprobar)
      window.clearInterval(intervalo)
    }
  }, [timeoutMinutes, onExpire])
}
