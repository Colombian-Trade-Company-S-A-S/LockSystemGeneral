// Configuración central de la app. Único punto que lee `import.meta.env`,
// para no dispersar accesos a variables de entorno por todo el código.

export const config = {
  /**
   * URL base de la API.
   * - En desarrollo queda vacía y se usa el proxy de Vite (`/api` -> Django).
   * - En producción se define `VITE_API_URL` con la URL completa del backend.
   */
  apiBaseUrl: import.meta.env.VITE_API_URL ?? '',

  /** Claves de almacenamiento en el navegador. */
  storage: {
    // Solo persiste el refresh token; el access token vive en memoria.
    refreshToken: 'ls.auth.refresh',
    // Marca de la última actividad del usuario (localStorage): la comparten
    // todas las pestañas para que la inactividad se cuente una sola vez.
    lastActivity: 'ls.auth.last-activity',
    // Motivo del último cierre de sesión (sessionStorage): lo lee el login
    // para avisar de que la sesión se cerró por inactividad.
    logoutReason: 'ls.auth.logout-reason',
    // Identificador de este navegador para la sesión única. A diferencia de los
    // tokens, sobrevive al logout: el navegador no cambia porque cambie quien
    // lo usa (ver src/lib/http/device.ts).
    deviceId: 'ls.device.id',
  },

  /** Cabecera con la que el navegador se identifica ante el backend. */
  deviceHeader: 'X-Device-Id',

  /** Rutas de la API de autenticación (centralizadas para no repetir strings). */
  endpoints: {
    login: '/api/auth/token/',
    refresh: '/api/auth/token/refresh/',
    logout: '/api/auth/logout/',
    changePassword: '/api/auth/password/',
    me: '/api/me/',
    // Diagnóstico de la integración con WhaleTV (solo correos autorizados).
    diagnosticoApi: '/api/diagnostico-api/',
  },
} as const
