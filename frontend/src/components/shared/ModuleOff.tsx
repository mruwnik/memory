import type { Module } from '@/hooks/useModule'
import { NAV_SECTIONS, activeItem } from '@/components/shared/nav'
import { ButtonLink, EmptyState, Page } from '@/components/shared/ui'

/** Stands in for a route whose module is off, so a bookmark or shared link says why instead of bouncing home. */
export const ModuleOff = ({ path, module, isAdmin }: { path: string; module: Module; isAdmin: boolean }) => {
  const title = activeItem(NAV_SECTIONS.flatMap(s => s.items), path, '')?.title ?? 'Unavailable'
  return (
    <Page title={title}>
      <EmptyState
        message={<>This page needs the <code>{module}</code> module, which is off on this server. {isAdmin ? 'Turn it on in Server settings.' : 'Ask an admin to turn it on.'}</>}
        action={isAdmin ? <ButtonLink to="/ui/server-settings">Open Server settings</ButtonLink> : undefined}
      />
    </Page>
  )
}
