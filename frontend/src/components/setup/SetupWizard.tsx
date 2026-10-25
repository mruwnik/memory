import { useEffect, useRef } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import type { AuthUser } from '@/hooks/useAuth'
import { Button, FormActions, Page, Section, cx } from '@/components/shared/ui'
import { AccountStep } from './AccountStep'
import { ClientStep } from './ClientStep'
import { KeysStep } from './KeysStep'
import { ModulesStep, VoyageStep } from './SettingsSteps'
import { SourcesStep } from './SourcesStep'
import { TimezoneStep } from './TimezoneStep'

interface Step {
  value: string
  title: string
  description: string
  /** Instance-wide, so only for admins. */
  admin?: boolean
}

const STEPS: Step[] = [
  {
    value: 'keys',
    title: 'LLM keys',
    description: 'An Anthropic or OpenAI key runs summaries and the LLM steps of search; agents need a key for their model\'s provider. Without one, content is still ingested, just without summaries.',
  },
  {
    value: 'voyage',
    title: 'Embeddings',
    description: 'A Voyage AI key embeds content for semantic search and reranks results. Without one, search matches keywords only.',
    admin: true,
  },
  {
    value: 'modules',
    title: 'Modules',
    description: 'Optional features, all off by default. Each applies once the api restarts; a module that is off hides its pages, MCP tools and scopes, and its data stays.',
    admin: true,
  },
  {
    value: 'sources',
    title: 'Connect a source',
    description: 'Pick what memory should ingest. You can add more later on the Sources page.',
  },
  {
    value: 'client',
    title: 'Connect an AI client',
    description: 'Your AI client searches and adds to memory through its MCP server.',
  },
  {
    value: 'timezone',
    title: 'Timezone',
    description: 'Used for your scheduled tasks and agent schedules.',
  },
]

const isAdmin = (user: AuthUser | null) => !!user?.scopes.includes('*')

interface Props {
  isAuthenticated: boolean
  user: AuthUser | null
  /** Re-reads the signed-in user, after the account is created or changed. */
  checkAuth: () => Promise<unknown>
}

export const SetupWizard = ({ isAuthenticated, user, checkAuth }: Props) => {
  const location = useLocation()
  const navigate = useNavigate()
  const steps = STEPS.filter(s => !s.admin || isAdmin(user))
  // The step lives only in this history entry, so once setup is left no URL leads back to it.
  const step = steps.findIndex(s => s.value === (location.state as { step?: string } | null)?.step)
  // flushSync: the router otherwise updates in a transition, so a sign-in landing first would see no step.
  const go = (index: number) => navigate('/ui/setup', { replace: true, state: { step: steps[index].value }, flushSync: true })
  const finish = () => navigate('/ui/dashboard', { replace: true })
  const heading = useRef<HTMLSpanElement>(null)

  // A step replaces the page in place, so start it at the top and tell screen readers where they are.
  useEffect(() => {
    document.documentElement.scrollTop = 0
    heading.current?.focus({ preventScroll: true })
  }, [step, isAuthenticated])

  if (!isAuthenticated) {
    return <AccountStep onCreated={async () => {
      await go(0)
      await checkAuth()
    }} />
  }
  if (step < 0 || !user) return <Navigate to="/ui/dashboard" replace />

  const current = steps[step]
  const last = step === steps.length - 1
  const back = <Button onClick={() => go(step - 1)}>Back</Button>
  return (
    <Page title="Set up Memory" description={`Step ${step + 1} of ${steps.length}. Every step can be skipped.`}
      actions={<Button variant="ghost" onClick={finish}>Leave setup</Button>}>
      <ol className="flex flex-wrap gap-x-6 gap-y-2 text-sm" aria-label="Setup steps">
        {steps.map((s, i) => (
          <li key={s.value} aria-current={i === step ? 'step' : undefined}
            className={cx(i === step ? 'font-semibold text-primary' : 'text-slate-600')}>
            {i + 1}. {s.title}
          </li>
        ))}
      </ol>
      <Section title={<span ref={heading} tabIndex={-1} className="outline-none">{current.title}</span>} description={current.description}>
        {current.value === 'keys' && <KeysStep />}
        {current.value === 'voyage' && <VoyageStep />}
        {current.value === 'modules' && <ModulesStep onRestarted={checkAuth} />}
        {current.value === 'sources' && <SourcesStep />}
        {current.value === 'client' && <ClientStep />}
        {current.value === 'timezone' && <TimezoneStep userId={user.id} onDone={() => checkAuth().then(finish)}
          actions={<>{back}<Button onClick={finish}>Finish without saving</Button></>} />}
      </Section>
      {!last && <FormActions>{step > 0 && back}<Button variant="primary" onClick={() => go(step + 1)}>Next</Button></FormActions>}
    </Page>
  )
}
