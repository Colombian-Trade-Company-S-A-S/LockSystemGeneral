import { useEffect, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Mail,
  ShieldCheck,
  Timer,
} from 'lucide-react'
import { useAuth } from '@/features/auth/context/auth-context'
import { config } from '@/lib/config'
import { ApiError } from '@/lib/http/errors'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface LocationState {
  from?: { pathname: string }
}

// Tema FIJO para el login: redefinimos localmente las variables que el sistema
// de acento (y el modo oscuro) sobrescriben en <html>, para que esta pantalla
// se vea siempre igual — monocromática blanco/negro — sin importar la
// configuración del usuario. Al vivir en el contenedor del login, estas
// variables ganan sobre las de <html> para todo su subárbol.
const STATIC_THEME: React.CSSProperties = {
  colorScheme: 'light',
  '--background': 'oklch(1 0 0)',
  '--foreground': 'oklch(0.145 0 0)',
  '--card': 'oklch(1 0 0)',
  '--card-foreground': 'oklch(0.145 0 0)',
  '--secondary': 'oklch(0.97 0 0)',
  '--secondary-foreground': 'oklch(0.205 0 0)',
  '--muted': 'oklch(0.97 0 0)',
  '--muted-foreground': 'oklch(0.556 0 0)',
  '--destructive': 'oklch(0.577 0.245 27.325)',
  '--border': 'oklch(0.922 0 0)',
  '--input': 'oklch(0.922 0 0)',
  // Acento monocromático: negro tinta, texto blanco.
  '--primary': 'oklch(0.145 0 0)',
  '--primary-foreground': 'oklch(1 0 0)',
  '--ring': 'oklch(0.145 0 0)',
} as React.CSSProperties

// Patrón de rejilla sutil para el panel negro (líneas blancas muy tenues),
// difuminado hacia los bordes con una máscara radial.
const GRID_PATTERN: React.CSSProperties = {
  backgroundImage:
    'linear-gradient(to right, rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.05) 1px, transparent 1px)',
  backgroundSize: '46px 46px',
  maskImage: 'radial-gradient(ellipse 80% 70% at 50% 40%, #000 40%, transparent 100%)',
  WebkitMaskImage:
    'radial-gradient(ellipse 80% 70% at 50% 40%, #000 40%, transparent 100%)',
}

function BrandLogo({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M3 7a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4c2.5.4 4 2.3 4 5 0 3-2.2 5-6 5-1.6 0-2.9-.4-3.9-1.2-.7.8-1.8 1.2-3.1 1.2H7a4 4 0 0 1-4-4V7Zm5 3a1.2 1.2 0 1 0 0 2.4A1.2 1.2 0 0 0 8 10Z" />
    </svg>
  )
}

const FEATURES = [
  'Inhabilitación remota de televisores',
  'Reportes y auditoría en tiempo real',
  'Control de pines y sincronizaciones',
]

export function LoginPage() {
  const { status, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [aviso, setAviso] = useState<string | null>(null)

  // Si llegamos aquí porque se cerró la sesión sola, explicamos por qué en vez
  // de dejar al usuario preguntándose qué pasó. La marca se consume al leerla.
  useEffect(() => {
    const motivo = sessionStorage.getItem(config.storage.logoutReason)
    if (!motivo) return
    sessionStorage.removeItem(config.storage.logoutReason)
    setAviso(
      motivo === 'idle'
        ? 'Tu sesión se cerró por inactividad. Ingresa de nuevo para continuar.'
        : 'Tu sesión se cerró desde el servidor. Puede que la hayan cerrado o que la cuenta se haya abierto en otro dispositivo.',
    )
  }, [])

  // Si ya hay sesión, no mostramos el login.
  if (status === 'authenticated') return <Navigate to="/" replace />

  const from = (location.state as LocationState | null)?.from?.pathname ?? '/'

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Credenciales inválidas. Revisa tu correo y contraseña.')
      } else if (err instanceof ApiError && err.status === 409) {
        // Sesión única: el backend explica si la cuenta ya está abierta en otro
        // dispositivo o si este navegador lo ocupa otra cuenta.
        setError(err.message)
      } else {
        setError('No fue posible iniciar sesión. Intenta de nuevo.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={STATIC_THEME} className="flex min-h-screen bg-background text-foreground">
      {/* Panel izquierdo (marca) — negro, oculto en móvil */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden bg-[#0a0a0a] p-14 text-white lg:flex">
        {/* Rejilla sutil */}
        <div className="pointer-events-none absolute inset-0" style={GRID_PATTERN} />
        {/* Halos difusos en blanco/gris para dar profundidad */}
        <div className="pointer-events-none absolute -top-32 -right-24 size-[26rem] rounded-full bg-white/[0.07] blur-3xl" />
        <div className="pointer-events-none absolute -bottom-40 -left-24 size-[26rem] rounded-full bg-white/[0.04] blur-3xl" />

        {/* Logo */}
        <div className="relative flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl bg-white/10 ring-1 ring-white/15 backdrop-blur">
            <BrandLogo className="size-6" />
          </div>
          <span className="text-xl font-extrabold tracking-tight">Locking System</span>
        </div>

        {/* Titular + features */}
        <div className="relative">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.06] px-3 py-1 text-xs font-medium text-white/70 backdrop-blur">
            <ShieldCheck className="size-3.5" />
            Plataforma de control
          </div>

          <h1 className="text-5xl font-extrabold leading-[1.05] tracking-tight">
            Control total de
            <br />
            tus televisores
          </h1>
          <p className="mt-5 max-w-sm text-lg text-white/60">
            Gestiona la inhabilitación de tus televisores Locking System desde un
            solo lugar.
          </p>

          <ul className="mt-9 flex flex-col gap-3.5">
            {FEATURES.map((f) => (
              <li key={f} className="flex items-center gap-3 text-white/80">
                <CheckCircle2 className="size-5 shrink-0 text-white" />
                <span className="text-sm">{f}</span>
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-xs text-white/40">
          © {new Date().getFullYear()} Locking System · Colombian Trade Company
        </p>
      </div>

      {/* Panel derecho (formulario) */}
      <div className="flex w-full items-center justify-center bg-muted/40 px-6 py-10 lg:w-1/2">
        <div className="w-full max-w-sm">
          {/* Marca compacta para móvil (el panel negro está oculto) */}
          <div className="mb-8 flex items-center justify-center gap-2.5 lg:hidden">
            <div className="flex size-10 items-center justify-center rounded-xl bg-[#0a0a0a] text-white shadow-sm">
              <BrandLogo className="size-5" />
            </div>
            <span className="text-lg font-extrabold tracking-tight text-foreground">
              Locking System
            </span>
          </div>

          <Card className="border-border/70 shadow-xl shadow-black/5">
            <CardHeader>
              <CardTitle className="text-xl font-bold">Iniciar sesión</CardTitle>
              <CardDescription>
                Ingresa con tu correo y contraseña para continuar.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
                {aviso && !error && (
                  <Alert>
                    <Timer />
                    <AlertDescription>{aviso}</AlertDescription>
                  </Alert>
                )}

                {error && (
                  <Alert variant="destructive">
                    <CircleAlert />
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                )}

                <div className="grid gap-2">
                  <Label htmlFor="email">Correo</Label>
                  <div className="relative">
                    <Mail className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="email"
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      required
                      autoFocus
                      placeholder="tu@correo.com"
                      autoComplete="email"
                      className="pl-9"
                    />
                  </div>
                </div>

                <div className="grid gap-2">
                  <Label htmlFor="password">Contraseña</Label>
                  <div className="relative">
                    <Lock className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      id="password"
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      placeholder="••••••••"
                      autoComplete="current-password"
                      className="pr-10 pl-9"
                    />
                    {/* Ojito SIN fondo (solo cambia el color al pasar el cursor) */}
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'}
                      aria-pressed={showPassword}
                      className="absolute top-1/2 right-2 flex size-7 -translate-y-1/2 items-center justify-center rounded-md bg-transparent text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:text-foreground"
                    >
                      {showPassword ? (
                        <EyeOff className="size-4" />
                      ) : (
                        <Eye className="size-4" />
                      )}
                    </button>
                  </div>
                </div>

                <Button type="submit" disabled={submitting} className="mt-1 w-full group">
                  {submitting && <Loader2 className="animate-spin" data-icon="inline-start" />}
                  {submitting ? 'Ingresando…' : 'Iniciar sesión'}
                  {!submitting && (
                    <ArrowRight
                      className="transition-transform group-hover:translate-x-0.5"
                      data-icon="inline-end"
                    />
                  )}
                </Button>
              </form>
            </CardContent>
          </Card>

          <p className="mt-6 flex items-center justify-center gap-1.5 text-center text-xs text-muted-foreground">
            <Lock className="size-3" />
            Conexión segura · Acceso restringido
          </p>
        </div>
      </div>
    </div>
  )
}
