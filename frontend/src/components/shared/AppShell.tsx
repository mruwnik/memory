import { useEffect, useRef, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import UserMenu from '@/components/common/UserMenu'
import type { AuthUser } from '@/hooks/useAuth'
import { cx, useDialogFocus, useEscape } from '@/components/shared/ui'
import { activeItem, visibleSections } from '@/components/shared/nav'
import { useModules } from '@/hooks/useModule'

interface AppShellProps {
  user: AuthUser | null
  onLogout: () => void
  hasScope: (scope: string) => boolean
}

const SectionNav = ({ hasScope }: { hasScope: (scope: string) => boolean }) => {
  const { pathname, search } = useLocation()
  const sections = visibleSections(hasScope, useModules())
  const current = activeItem(sections.flatMap(s => s.items), pathname, search)
  return (
    <div className="py-4 space-y-5">
      {sections.map(section => (
        <div key={section.title}>
          <h2 className="px-4 mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{section.title}</h2>
          <ul>
            {section.items.map(item => (
              <li key={item.to}>
                <Link to={item.to} aria-current={item === current ? 'page' : undefined}
                  className={cx('block mx-2 px-2 py-1.5 rounded-md text-sm transition-colors',
                    item === current ? 'bg-primary/10 text-primary-dark font-medium' : 'text-slate-700 hover:bg-slate-100')}>
                  {item.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

/** Top bar and section sidebar shared by every signed-in page; the page itself renders through <Page>. */
export const AppShell = ({ user, onLogout, hasScope }: AppShellProps) => {
  const [navOpen, setNavOpen] = useState(false)
  const { pathname, search } = useLocation()
  const navRef = useRef<HTMLElement>(null)

  useEffect(() => setNavOpen(false), [pathname, search])

  // The drawer is only a dialog below lg; leaving it open on resize would keep dialog semantics on the visible sidebar.
  useEffect(() => {
    const wide = window.matchMedia?.('(min-width: 1024px)')
    if (!wide) return
    const close = (e: MediaQueryListEvent) => e.matches && setNavOpen(false)
    wide.addEventListener('change', close)
    return () => wide.removeEventListener('change', close)
  }, [])

  useDialogFocus(navRef, () => navRef.current?.querySelector<HTMLElement>('a'), navOpen)
  useEscape(navRef, () => setNavOpen(false), navOpen)

  return (
    <>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:bg-white focus:px-3 focus:py-2 focus:rounded">
        Skip to content
      </a>
      <header className="sticky top-0 z-30 h-14 bg-white border-b border-slate-200">
        <div className="h-full px-4 md:px-6 flex items-center gap-3">
          <button type="button" className="lg:hidden -ml-2 w-10 h-10 flex items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100"
            aria-label="Sections" aria-expanded={navOpen} aria-controls="section-nav" onClick={() => setNavOpen(o => !o)}>
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeWidth={2} d={navOpen ? 'M6 6l12 12M18 6L6 18' : 'M4 6h16M4 12h16M4 18h16'} />
            </svg>
          </button>
          <Link to="/ui/dashboard" className="text-lg font-semibold text-primary">Memory</Link>
          <div className="ml-auto">{user && <UserMenu user={user} onLogout={onLogout} hasScope={hasScope} />}</div>
        </div>
      </header>
      <div className="flex flex-1">
        {/* Narrow screens: a drawer over the page, toggled from the top bar. Wide screens: a fixed column. */}
        {navOpen && <div className="lg:hidden fixed inset-0 top-14 z-30 bg-black/30" onClick={() => setNavOpen(false)} />}
        <nav id="section-nav" ref={navRef} aria-label="Sections" role={navOpen ? 'dialog' : undefined} aria-modal={navOpen || undefined}
          className={cx('w-60 shrink-0 bg-white border-r border-slate-200 overflow-y-auto',
            'fixed top-14 bottom-0 left-0 z-40 lg:sticky lg:z-auto lg:h-[calc(100dvh-3.5rem)] lg:block',
            navOpen ? 'block shadow-xl' : 'hidden')}>
          <SectionNav hasScope={hasScope} />
        </nav>
        <main id="main" tabIndex={-1} className="flex-1 min-w-0 focus:outline-none">
          <Outlet />
        </main>
      </div>
    </>
  )
}
