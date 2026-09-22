// Hooks de React Query para la gestión de usuarios (solo Administrador).
// Mismo patrón que televisores: claves jerárquicas + mutaciones que invalidan,
// así un usuario nuevo o un cambio de rol se ve al instante en la lista.

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import {
  usuariosApi,
  type UsuarioCreateInput,
  type UsuarioUpdateInput,
} from '@/features/usuarios/api/usuarios.api'

export const usuariosKeys = {
  all: ['usuarios'] as const,
  list: (search: string, page: number) =>
    ['usuarios', 'list', { search, page }] as const,
  detail: (id: string) => ['usuarios', 'detail', id] as const,
}

export function useUsuarios(search: string, page: number) {
  return useQuery({
    queryKey: usuariosKeys.list(search, page),
    queryFn: () => usuariosApi.list(search, page),
    placeholderData: keepPreviousData,
  })
}

export function useUsuario(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: usuariosKeys.detail(id ?? ''),
    queryFn: () => usuariosApi.get(id!),
    enabled: enabled && !!id,
  })
}

export function useCrearUsuario() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: UsuarioCreateInput) => usuariosApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: usuariosKeys.all })
    },
  })
}

export function useActualizarUsuario(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: UsuarioUpdateInput) => usuariosApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: usuariosKeys.all })
    },
  })
}

export function useCerrarSesionUsuario() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => usuariosApi.cerrarSesion(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: usuariosKeys.all })
    },
  })
}
