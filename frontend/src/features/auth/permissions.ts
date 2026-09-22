// Política de permisos del frontend (espejo de `users/permissions.py` en el
// backend). Aquí solo se decide qué se MUESTRA; la autorización real la impone
// el backend. Mantener ambas listas alineadas.

import type { Role, User } from '@/features/auth/types'

/**
 * Administrador general: el único que ve todas las empresas y puede crearlas.
 * Los demás (incluido el rol `admin`) están confinados a la suya.
 */
export function isSuperAdmin(user: User | null): boolean {
  return user?.is_superadmin === true
}

/** Administrador: gestión de usuarios + todos los módulos (dentro de su empresa). */
export function isAdmin(user: User | null): boolean {
  return user?.role === 'admin' || isSuperAdmin(user)
}

/**
 * Puede realizar gestiones de escritura sobre los datos (crear/editar TV,
 * cambiar estados, enrolar, sincronizar).
 *
 * El administrador general queda FUERA: es un auditor de solo lectura sobre los
 * datos de las empresas (los ve y exporta todos, pero no los altera). Su poder
 * está en otro plano —usuarios y empresas— que no pasa por aquí. Espejo de
 * `User.can_operate` en el backend.
 */
export function canOperate(user: User | null): boolean {
  if (isSuperAdmin(user)) return false
  return user?.role === 'operador' || user?.role === 'admin'
}

/**
 * Diagnóstico de la API de WhaleTV: herramienta de soporte, no del producto.
 * La lista real vive en `settings.DIAGNOSTICO_API_EMAILS` del backend, que es
 * quien autoriza de verdad; aquí solo se decide si se muestra la pestaña.
 */
const CORREOS_DIAGNOSTICO = ['palaciosjulian286@gmail.com']

export function puedeDiagnosticarApi(user: User | null): boolean {
  const correo = user?.email?.trim().toLowerCase()
  return !!correo && CORREOS_DIAGNOSTICO.includes(correo)
}

export const ROLE_LABELS: Record<Role, string> = {
  admin: 'Administrador',
  operador: 'Operador',
  consulta: 'Consulta',
}
