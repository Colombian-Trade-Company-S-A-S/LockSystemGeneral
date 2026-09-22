// Capa de acceso a la API de configuración de la cuenta propia.

import { config } from '@/lib/config'
import { apiFetch } from '@/lib/http/client'
import { tokenStore } from '@/lib/http/tokens'
import type { User } from '@/features/auth/types'

interface TokenPair {
  access: string
  refresh: string
}

/** Una comprobación individual del diagnóstico de la API. */
export interface PruebaApi {
  nombre: string
  ok: boolean
  detalle: string
  ms: number | null
}

export interface DiagnosticoApi {
  ok: boolean
  usa_api: boolean
  host: string
  brand_id: string
  access_key: string
  pruebas: PruebaApi[]
  resumen: string
}

export const settingsApi = {
  /** Comprueba en vivo si la API de WhaleTV responde. Solo lectura. */
  probarApi: () => apiFetch<DiagnosticoApi>(config.endpoints.diagnosticoApi),

  /** Edita el perfil propio (nombre/apellido). Devuelve el usuario actualizado. */
  updateProfile: (data: { first_name: string; last_name: string }) =>
    apiFetch<User>(config.endpoints.me, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  /** Guarda la preferencia de color de acento en la cuenta del usuario. */
  updateAccent: (accent: string) =>
    apiFetch<User>(config.endpoints.me, {
      method: 'PATCH',
      body: JSON.stringify({ accent }),
    }),

  /**
   * Cambia la contraseña. El backend revoca las sesiones anteriores y devuelve
   * un par de tokens nuevo para la sesión actual: lo guardamos para no salir.
   */
  async changePassword(
    current_password: string,
    new_password: string,
  ): Promise<void> {
    const tokens = await apiFetch<TokenPair>(config.endpoints.changePassword, {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    })
    tokenStore.setAccess(tokens.access)
    tokenStore.setRefresh(tokens.refresh)
  },
}
