// Provider que mantiene el estado de sesión y lo expone vía contexto.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { authApi } from '@/features/auth/api/auth.api'
import { AuthContext, type AuthContextValue } from '@/features/auth/context/auth-context'
import type { AuthStatus, User } from '@/features/auth/types'
import { useIdleLogout } from '@/features/auth/useIdleLogout'
import { applyAccentKey } from '@/features/settings/accent'
import { config } from '@/lib/config'
import { onSesionPerdida } from '@/lib/http/client'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')

  // Al montar: intenta restaurar la sesión a partir del refresh token guardado.
  useEffect(() => {
    let active = true
    authApi.restore().then((restored) => {
      if (!active) return
      setUser(restored)
      setStatus(restored ? 'authenticated' : 'unauthenticated')
      // Arranca con el acento guardado en la cuenta (fuente de verdad).
      if (restored) applyAccentKey(restored.accent)
    })
    return () => {
      active = false
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const loggedIn = await authApi.login(email, password)
    setUser(loggedIn)
    setStatus('authenticated')
    // Al entrar, aplica el acento que el usuario dejó guardado en su cuenta.
    applyAccentKey(loggedIn.accent)
  }, [])

  const logout = useCallback(() => {
    authApi.logout()
    setUser(null)
    setStatus('unauthenticated')
  }, [])

  // Si el servidor da la sesión por terminada (la cerró un administrador, o la
  // cuenta se abrió en otro dispositivo), volvemos al login en el acto en lugar
  // de dejar al usuario peleando con errores.
  useEffect(
    () =>
      onSesionPerdida(() => {
        authApi.discard()
        setUser(null)
        setStatus('unauthenticated')
        sessionStorage.setItem(config.storage.logoutReason, 'servidor')
      }),
    [],
  )

  // Cierre por inactividad: el plazo lo manda el backend en el perfil
  // (SESSION_IDLE_TIMEOUT_MINUTES). Al vencer, <ProtectedRoute> va al login.
  useIdleLogout(status === 'authenticated' ? user?.session_timeout_minutes : 0, logout)

  const refreshUser = useCallback(async () => {
    setUser(await authApi.me())
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, logout, refreshUser }),
    [user, status, login, logout, refreshUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
