// Capa de acceso a la API de autenticación. Encapsula los endpoints y el
// almacenamiento de tokens para que el resto de la app no conozca esos detalles.

import { config } from '@/lib/config'
import { apiFetch, refreshSession } from '@/lib/http/client'
import { setDeviceId } from '@/lib/http/device'
import { tokenStore } from '@/lib/http/tokens'
import type { AuthTokens, User } from '@/features/auth/types'

export const authApi = {
  /** Inicia sesión con email + contraseña y devuelve el perfil del usuario. */
  async login(email: string, password: string): Promise<User> {
    const tokens = await apiFetch<AuthTokens>(config.endpoints.login, {
      method: 'POST',
      auth: false,
      body: JSON.stringify({ email, password }),
    })
    tokenStore.setAccess(tokens.access)
    tokenStore.setRefresh(tokens.refresh)
    // El backend confirma con qué dispositivo quedó atada la sesión.
    setDeviceId(tokens.device_id)
    return authApi.me()
  },

  /** Perfil del usuario autenticado. */
  me: (): Promise<User> => apiFetch<User>(config.endpoints.me),

  /**
   * Restaura la sesión al arrancar la app: si hay refresh token guardado,
   * obtiene un access nuevo y devuelve el perfil. Null si no hay sesión válida.
   */
  async restore(): Promise<User | null> {
    if (!tokenStore.getRefresh()) return null
    const ok = await refreshSession()
    if (!ok) return null
    try {
      return await authApi.me()
    } catch {
      tokenStore.clear()
      return null
    }
  },

  /**
   * Descarta la sesión SOLO en el cliente, sin llamar al backend.
   * Se usa cuando es el servidor quien la dio por terminada (la cerró un
   * administrador, caducó, o la cuenta se abrió en otro dispositivo): pedirle
   * un logout con tokens que ya rechazó no aporta nada.
   */
  discard(): void {
    tokenStore.clear()
  },

  /**
   * Cierra la sesión. Best-effort: pide al backend revocar los tokens del
   * usuario (logout real server-side) y, pase lo que pase, los descarta
   * localmente. No lanza: el cierre local nunca debe fallar por la red.
   */
  async logout(): Promise<void> {
    try {
      await apiFetch<void>(config.endpoints.logout, { method: 'POST' })
    } catch {
      // El servidor pudo estar caído o el token vencido: da igual, limpiamos.
    } finally {
      tokenStore.clear()
    }
  },
}
