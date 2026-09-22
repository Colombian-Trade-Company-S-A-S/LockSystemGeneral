import { useState } from 'react'
import {
  Check,
  CircleAlert,
  CircleCheck,
  Info,
  Loader2,
  Lock,
  Moon,
  Palette,
  Stethoscope,
  Sun,
  User,
} from 'lucide-react'
import { useAuth } from '@/features/auth/context/auth-context'
import { puedeDiagnosticarApi } from '@/features/auth/permissions'
import { settingsApi } from '@/features/settings/api/settings.api'
import { DiagnosticoApiPanel } from '@/features/settings/components/DiagnosticoApiPanel'
import { ACCENTS, useAccent } from '@/features/settings/accent'
import { useLayoutPrefs } from '@/shared/layout/useLayoutPrefs'
import { ApiError } from '@/lib/http/errors'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

/** Extrae errores por campo y un mensaje general de un ApiError de DRF. */
function parseErrors(err: unknown): {
  fields: Record<string, string>
  general: string | null
} {
  if (err instanceof ApiError && err.data && typeof err.data === 'object') {
    const data = err.data as Record<string, unknown>
    const fields: Record<string, string> = {}
    let general: string | null = null
    for (const [key, val] of Object.entries(data)) {
      const msg = Array.isArray(val) ? String(val[0]) : String(val)
      if (key === 'detail' || key === 'non_field_errors') general = msg
      else fields[key] = msg
    }
    return { fields, general }
  }
  return { fields: {}, general: (err as Error)?.message ?? 'Error inesperado.' }
}

function FieldError({ msg }: { msg?: string }) {
  if (!msg) return null
  return <p className="text-xs text-destructive">{msg}</p>
}

function ErrorAlert({ msg }: { msg: string | null }) {
  if (!msg) return null
  return (
    <Alert variant="destructive">
      <CircleAlert />
      <AlertTitle>No se pudo completar</AlertTitle>
      <AlertDescription>{msg}</AlertDescription>
    </Alert>
  )
}

function SuccessAlert({ show, children }: { show: boolean; children: React.ReactNode }) {
  if (!show) return null
  return (
    <Alert className="border-emerald-500/30 text-emerald-600 dark:text-emerald-400">
      <CircleCheck />
      <AlertDescription className="text-emerald-600 dark:text-emerald-400">
        {children}
      </AlertDescription>
    </Alert>
  )
}

// ---------------------------------------------------------------------------
// Perfil
// ---------------------------------------------------------------------------
function PerfilPanel() {
  const { user, refreshUser } = useAuth()
  const [firstName, setFirstName] = useState(user?.first_name ?? '')
  const [lastName, setLastName] = useState(user?.last_name ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [general, setGeneral] = useState<string | null>(null)
  const [ok, setOk] = useState(false)
  const [saving, setSaving] = useState(false)

  const dirty =
    firstName !== (user?.first_name ?? '') || lastName !== (user?.last_name ?? '')

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setErrors({})
    setGeneral(null)
    setOk(false)
    try {
      await settingsApi.updateProfile({ first_name: firstName, last_name: lastName })
      await refreshUser()
      setOk(true)
    } catch (err) {
      const { fields, general: g } = parseErrors(err)
      setErrors(fields)
      setGeneral(g)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader className="border-b pb-4">
        <CardTitle>Perfil</CardTitle>
        <CardDescription>Actualiza tu información personal.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate className="max-w-lg space-y-5">
          <ErrorAlert msg={general} />
          <SuccessAlert show={ok}>Perfil actualizado.</SuccessAlert>

          <div className="grid gap-2">
            <Label htmlFor="email">Correo electrónico</Label>
            <Input id="email" value={user?.email ?? ''} disabled />
            <p className="text-xs text-muted-foreground">
              El correo no se puede cambiar.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="empresa">Empresa</Label>
            <Input
              id="empresa"
              value={user?.empresa?.nombre ?? 'Todas las empresas'}
              disabled
            />
            <p className="text-xs text-muted-foreground">
              {user?.empresa
                ? 'Solo ves los televisores y registros de tu empresa.'
                : 'Como administrador general ves todas las empresas.'}
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="first_name">Nombres</Label>
              <Input
                id="first_name"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
              />
              <FieldError msg={errors.first_name} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="last_name">Apellidos</Label>
              <Input
                id="last_name"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
              />
              <FieldError msg={errors.last_name} />
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button type="submit" disabled={saving || !dirty}>
              {saving && <Loader2 className="animate-spin" />}
              {saving ? 'Guardando…' : 'Guardar cambios'}
            </Button>
            {!dirty && !ok && (
              <span className="text-xs text-muted-foreground">
                No hay cambios por guardar.
              </span>
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Seguridad (contraseña)
// ---------------------------------------------------------------------------
function SeguridadPanel() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [general, setGeneral] = useState<string | null>(null)
  const [ok, setOk] = useState(false)
  const [saving, setSaving] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setErrors({})
    setGeneral(null)
    setOk(false)

    if (next !== confirm) {
      setErrors({ confirm: 'Las contraseñas no coinciden.' })
      return
    }

    setSaving(true)
    try {
      await settingsApi.changePassword(current, next)
      setOk(true)
      setCurrent('')
      setNext('')
      setConfirm('')
    } catch (err) {
      const { fields, general: g } = parseErrors(err)
      setErrors(fields)
      setGeneral(g)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader className="border-b pb-4">
        <CardTitle>Seguridad</CardTitle>
        <CardDescription>Cambia tu contraseña de acceso.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate className="max-w-lg space-y-5">
          <ErrorAlert msg={general} />
          <SuccessAlert show={ok}>Contraseña actualizada.</SuccessAlert>

          <div className="grid gap-2">
            <Label htmlFor="current_password">Contraseña actual</Label>
            <Input
              id="current_password"
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              autoComplete="current-password"
            />
            <FieldError msg={errors.current_password} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="new_password">Nueva contraseña</Label>
              <Input
                id="new_password"
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                autoComplete="new-password"
                placeholder="Mínimo 10 caracteres"
              />
              <FieldError msg={errors.new_password} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="confirm_password">Confirmar contraseña</Label>
              <Input
                id="confirm_password"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
              />
              <FieldError msg={errors.confirm} />
            </div>
          </div>

          <Alert>
            <Info />
            <AlertDescription>
              Al cambiar la contraseña se cerrarán tus demás sesiones por seguridad;
              la sesión actual se mantiene.
            </AlertDescription>
          </Alert>

          <Button type="submit" disabled={saving || !current || !next || !confirm}>
            {saving && <Loader2 className="animate-spin" />}
            {saving ? 'Cambiando…' : 'Cambiar contraseña'}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Apariencia (tema + color de acento)
// ---------------------------------------------------------------------------
function AparienciaPanel() {
  const { dark, setTheme } = useLayoutPrefs()
  const { key, setAccent } = useAccent()
  const { refreshUser } = useAuth()

  // Aplica el acento al instante (CSS + caché local) y lo guarda en la cuenta,
  // para que la app arranque con ese color en cualquier navegador o dispositivo.
  const elegirAccent = (a: (typeof ACCENTS)[number]) => {
    setAccent(a)
    settingsApi
      .updateAccent(a.key)
      .then(refreshUser)
      .catch(() => {
        // La preferencia ya se aplicó localmente; si falla el guardado remoto
        // (p. ej. sin red) no interrumpimos la experiencia.
      })
  }

  return (
    <Card>
      <CardHeader className="border-b pb-4">
        <CardTitle>Apariencia</CardTitle>
        <CardDescription>Personaliza cómo se ve la aplicación.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-7">
          <div className="grid gap-2">
            <Label>Tema</Label>
            <div className="grid max-w-md grid-cols-2 gap-3">
              <Button
                type="button"
                variant={dark ? 'outline' : 'default'}
                onClick={() => setTheme(false)}
              >
                <Sun /> Claro
              </Button>
              <Button
                type="button"
                variant={dark ? 'default' : 'outline'}
                onClick={() => setTheme(true)}
              >
                <Moon /> Oscuro
              </Button>
            </div>
          </div>

          <div className="grid gap-2">
            <Label>Color de acento</Label>
            <div className="flex flex-wrap gap-3">
              {ACCENTS.map((a) => {
                const active = a.key === key
                return (
                  <button
                    key={a.key}
                    type="button"
                    onClick={() => elegirAccent(a)}
                    title={a.name}
                    aria-label={a.name}
                    className={cn(
                      'flex size-10 items-center justify-center rounded-full transition',
                      active
                        ? 'ring-2 ring-foreground ring-offset-2 ring-offset-background'
                        : 'ring-1 ring-border hover:scale-110',
                    )}
                    style={{
                      background: `linear-gradient(135deg, ${a.light}, ${a.dark})`,
                    }}
                  >
                    {active && <Check className="size-4" style={{ color: a.fg }} />}
                  </button>
                )
              })}
            </div>
            <p className="text-xs text-muted-foreground">
              Se aplica al instante y se guarda en tu cuenta para cualquier dispositivo.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Página: tabs verticales (nav) + panel
// ---------------------------------------------------------------------------
export function SettingsPage() {
  // Solo decide si se MUESTRA; quien autoriza de verdad es el backend
  // (settings.DIAGNOSTICO_API_EMAILS), que devuelve 403 a los demás.
  const { user } = useAuth()
  const puedeDiagnostico = puedeDiagnosticarApi(user)

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-6">
        <h2 className="text-xl font-bold text-foreground">Configuración</h2>
        <p className="text-sm text-muted-foreground">
          Administra tu cuenta y la apariencia de la app.
        </p>
      </div>

      <Tabs defaultValue="perfil" orientation="vertical" className="gap-6">
        <TabsList variant="line" className="w-48 shrink-0">
          <TabsTrigger value="perfil">
            <User /> Perfil
          </TabsTrigger>
          <TabsTrigger value="seguridad">
            <Lock /> Seguridad
          </TabsTrigger>
          <TabsTrigger value="apariencia">
            <Palette /> Apariencia
          </TabsTrigger>
          {puedeDiagnostico && (
            <TabsTrigger value="diagnostico">
              <Stethoscope /> Estado API
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="perfil">
          <PerfilPanel />
        </TabsContent>
        <TabsContent value="seguridad">
          <SeguridadPanel />
        </TabsContent>
        <TabsContent value="apariencia">
          <AparienciaPanel />
        </TabsContent>
        {puedeDiagnostico && (
          <TabsContent value="diagnostico">
            <DiagnosticoApiPanel />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
