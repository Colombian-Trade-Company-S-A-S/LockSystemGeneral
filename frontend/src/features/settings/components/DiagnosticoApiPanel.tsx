import { useState } from 'react'
import {
  CircleAlert,
  CircleCheck,
  Loader2,
  RefreshCw,
  Stethoscope,
} from 'lucide-react'
import {
  settingsApi,
  type DiagnosticoApi,
} from '@/features/settings/api/settings.api'
import { ApiError } from '@/lib/http/errors'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { cn } from '@/lib/utils'

function mensajeError(err: unknown): string {
  if (err instanceof ApiError && err.data && typeof err.data === 'object') {
    const data = err.data as Record<string, unknown>
    const first = data.detail ?? Object.values(data)[0]
    if (first) return Array.isArray(first) ? String(first[0]) : String(first)
  }
  return (err as Error)?.message ?? 'No se pudo completar la prueba.'
}

export function DiagnosticoApiPanel() {
  const [cargando, setCargando] = useState(false)
  const [resultado, setResultado] = useState<DiagnosticoApi | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [probadoEn, setProbadoEn] = useState<Date | null>(null)

  async function probar() {
    setCargando(true)
    setError(null)
    try {
      setResultado(await settingsApi.probarApi())
      setProbadoEn(new Date())
    } catch (err) {
      setResultado(null)
      setError(mensajeError(err))
    } finally {
      setCargando(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Stethoscope className="size-4" aria-hidden />
          Estado de la API de WhaleTV
        </CardTitle>
        <CardDescription>
          Comprueba en vivo si el bloqueo y desbloqueo de televisores está
          funcionando por la API. Es solo de lectura: no modifica ningún equipo.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={probar} disabled={cargando}>
            {cargando ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : resultado ? (
              <RefreshCw className="size-4" aria-hidden />
            ) : (
              <Stethoscope className="size-4" aria-hidden />
            )}
            {cargando
              ? 'Probando…'
              : resultado
                ? 'Volver a probar'
                : 'Probar API'}
          </Button>
          {probadoEn && !cargando && (
            <span className="text-xs text-muted-foreground">
              Última prueba: {probadoEn.toLocaleTimeString('es-CO')}
            </span>
          )}
        </div>

        {error && (
          <Alert variant="destructive">
            <CircleAlert className="size-4" aria-hidden />
            <AlertTitle>No se pudo probar</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {resultado && (
          <>
            <Alert variant={resultado.ok ? 'default' : 'destructive'}>
              {resultado.ok ? (
                <CircleCheck className="size-4" aria-hidden />
              ) : (
                <CircleAlert className="size-4" aria-hidden />
              )}
              <AlertTitle>
                {resultado.ok ? 'Funcionando' : 'Con problemas'}
              </AlertTitle>
              <AlertDescription>{resultado.resumen}</AlertDescription>
            </Alert>

            <ul className="divide-y rounded-md border">
              {resultado.pruebas.map((p) => (
                <li
                  key={p.nombre}
                  className="flex items-start gap-3 px-3 py-2.5 text-sm"
                >
                  {p.ok ? (
                    <CircleCheck
                      className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400"
                      aria-hidden
                    />
                  ) : (
                    <CircleAlert
                      className="mt-0.5 size-4 shrink-0 text-destructive"
                      aria-hidden
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{p.nombre}</span>
                      {p.ms != null && (
                        <Badge variant="secondary" className="font-mono text-[10px]">
                          {p.ms} ms
                        </Badge>
                      )}
                    </div>
                    <p
                      className={cn(
                        'text-muted-foreground',
                        !p.ok && 'text-destructive',
                      )}
                    >
                      {p.detalle}
                    </p>
                  </div>
                  <span className="sr-only">{p.ok ? 'Correcto' : 'Con error'}</span>
                </li>
              ))}
            </ul>

            <dl className="grid gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-[auto_1fr]">
              <dt>Servidor</dt>
              <dd className="font-mono break-all">{resultado.host}</dd>
              <dt>Access Key</dt>
              <dd className="font-mono break-all">{resultado.access_key}</dd>
              <dt>Brand ID</dt>
              <dd className="font-mono break-all">{resultado.brand_id}</dd>
              <dt>Camino en uso</dt>
              <dd>
                {resultado.usa_api
                  ? 'API de WhaleTV (Selenium queda de respaldo)'
                  : 'Selenium (la API está desactivada)'}
              </dd>
            </dl>
          </>
        )}
      </CardContent>
    </Card>
  )
}
