// Tipos del dominio de autenticación (alineados con la API de Django `users`).

export type Role = 'admin' | 'operador' | 'consulta'

/** Empresa (tenant) tal como viene anidada en el perfil. */
export interface EmpresaBreve {
  id: string
  nombre: string
}

export interface User {
  id: string
  email: string
  first_name: string
  last_name: string
  full_name: string
  role: Role
  role_display: string
  /**
   * Empresa a la que pertenece la cuenta: define QUÉ datos ve (el rol define
   * qué puede hacer). Es `null` solo en el administrador general.
   */
  empresa: EmpresaBreve | null
  /** Administrador general: ve y gestiona todas las empresas. */
  is_superadmin: boolean
  is_active: boolean
  date_joined: string
  /** Preferencia de color de acento (clave del preset, p. ej. 'neutro'). */
  accent: string
  /**
   * Minutos de inactividad permitidos antes de cerrar la sesión. Lo decide el
   * backend (SESSION_IDLE_TIMEOUT_MINUTES en su `.env`) y lo impone él mismo;
   * aquí solo se usa para llevar al usuario al login a tiempo. 0 = desactivado.
   * Solo viaja en /api/me/: en los listados de usuarios no viene.
   */
  session_timeout_minutes?: number
  /** Sesión única: la cuenta tiene ahora mismo una sesión ocupando un navegador. */
  sesion_activa: boolean
  /** Navegador donde quedó abierta, en legible ("Chrome en Windows"). */
  sesion_dispositivo: string
  /** Cuándo se abrió esa sesión. */
  session_started_at: string | null
}

export interface AuthTokens {
  access: string
  refresh: string
  /** Dispositivo con el que el backend ató la sesión (ver sesión única). */
  device_id?: string
}

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'
