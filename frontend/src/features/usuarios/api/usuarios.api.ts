// Capa de acceso a la API de gestión de usuarios (solo Administrador).

import { apiFetch } from '@/lib/http/client'
import type { Role, User } from '@/features/auth/types'
import type { Paginated } from '@/shared/types'

export interface UsuarioCreateInput {
  email: string
  password: string
  first_name?: string
  last_name?: string
  role: Role
  /**
   * Solo la usa el administrador general, que no tiene empresa propia y debe
   * decir a cuál pertenece el usuario. A un admin de empresa el backend le
   * ignora este campo y usa la suya.
   */
  empresa?: string
}

export interface UsuarioUpdateInput {
  first_name?: string
  last_name?: string
  role?: Role
  is_active?: boolean
  /** Solo el administrador general puede moverlo de empresa (el backend lo exige). */
  empresa?: string
}

export const usuariosApi = {
  list: (search = '', page = 1) => {
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    if (page > 1) params.set('page', String(page))
    const qs = params.toString()
    return apiFetch<Paginated<User>>(`/api/usuarios/${qs ? `?${qs}` : ''}`)
  },

  get: (id: string) => apiFetch<User>(`/api/usuarios/${id}/`),

  create: (data: UsuarioCreateInput) =>
    apiFetch<User>('/api/usuarios/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: string, data: UsuarioUpdateInput) =>
    apiFetch<User>(`/api/usuarios/${id}/`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  /**
   * Fuerza el cierre de la sesión de un usuario y libera su cuenta.
   * Es la salida para quien dejó la sesión colgada en un equipo al que ya no
   * llega: sin esto habría que esperar a que caduque por inactividad.
   */
  cerrarSesion: (id: string) =>
    apiFetch<User>(`/api/usuarios/${id}/cerrar-sesion/`, { method: 'POST' }),
}
