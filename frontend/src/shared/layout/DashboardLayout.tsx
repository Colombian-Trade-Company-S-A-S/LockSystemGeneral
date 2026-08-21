// Chrome de la aplicación autenticada: sidebar shadcn (SidebarProvider) + área de
// contenido (SidebarInset) con barra superior de trigger + breadcrumb.

import { useEffect, useState } from 'react'
import { Outlet, useMatches } from 'react-router-dom'
import { Search } from 'lucide-react'
import { AppSidebar } from '@/shared/layout/Sidebar'
import { CommandPalette } from '@/shared/command/CommandPalette'
import { Separator } from '@/components/ui/separator'
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar'

interface RouteHandle {
  breadcrumb?: string
}

export function DashboardLayout() {
  const matches = useMatches()
  const [cmdOpen, setCmdOpen] = useState(false)

  // Atajo global para abrir la paleta: Ctrl+K (Windows/Linux) o ⌘+K (Mac).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setCmdOpen((v) => !v)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // Toma el breadcrumb de la ruta más profunda que lo defina.
  const breadcrumb =
    [...matches]
      .reverse()
      .map((m) => (m.handle as RouteHandle | undefined)?.breadcrumb)
      .find(Boolean) ?? 'Inicio'

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
          <span className="text-sm font-medium text-whale">{breadcrumb}</span>

          {/* Buscador global: abre la paleta (Ctrl/⌘ + K). */}
          <button
            type="button"
            onClick={() => setCmdOpen(true)}
            className="ml-auto flex h-8 items-center gap-2 rounded-lg border bg-background px-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <Search className="size-4" />
            <span className="hidden sm:inline">Buscar…</span>
            <kbd className="hidden items-center gap-0.5 rounded border bg-muted px-1 font-sans text-[10px] font-medium sm:inline-flex">
              Ctrl K
            </kbd>
          </button>
        </header>
        <div className="flex-1 p-6">
          <Outlet />
        </div>
      </SidebarInset>

      <CommandPalette open={cmdOpen} onOpenChange={setCmdOpen} />
    </SidebarProvider>
  )
}
