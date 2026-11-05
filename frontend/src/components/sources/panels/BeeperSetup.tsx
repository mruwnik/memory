import { useEffect, useState } from 'react'
import { useBeeper, BeeperAccount, BeeperNetwork, BeeperServer, BeeperSetup as SetupState, BeeperSetupStep, BeeperVerification, BeeperVerificationAction } from '@/hooks/useBeeper'
import { Button, Checkbox, Field, FormActions, Hint, Input, List, Modal, Notice, Row, Select, useSubmit } from '@/components/shared/ui'

type Step = 'server' | BeeperSetupStep

const STEPS: Record<Step, string> = {
  server: 'Beeper Server',
  email: 'Email',
  code: 'Code',
  verify: 'Approve this device',
  done: 'Done',
}

const POLL_MS = 2000

const FINISHED = ['done', 'cancelled', 'error']

// Beeper's names for the actions a verification offers now
type AvailableAction = 'accept' | 'cancel' | 'qr.confirmScanned' | 'sas.start' | 'sas.confirm'

const can = (verification: BeeperVerification, action: AvailableAction) => verification.available_actions.includes(action)

export const CloudNote = () => (
  <Notice tone="info">
    Memory sees only the networks you connect with <strong>Beeper Cloud</strong> in the Beeper app.
    Networks connected on a device stay on that device and never reach memory.
  </Notice>
)

interface BeeperSetupProps {
  // null adds a new account; otherwise sign-in resumes at the account's step
  account: BeeperAccount | null
  onClose: () => void
}

export const BeeperSetup = ({ account: initial, onClose }: BeeperSetupProps) => {
  const { createAccount, getAccount, sendEmail, sendCode } = useBeeper()
  const [account, setAccount] = useState(initial)
  const [step, setStep] = useState<Step>(initial?.step ?? 'server')
  const [verification, setVerification] = useState<BeeperVerification | null>(null)
  const action = useSubmit()

  const apply = (setup: SetupState) => {
    setAccount(setup.account)
    setVerification(setup.verification)
    setStep(setup.account.step)
  }

  const handleCreate = (data: Parameters<typeof createAccount>[0]) => action.submit(async () => {
    const created = await createAccount(data)
    setAccount(created)
    setStep(created.step)
  })

  const handleEmail = (email: string) => action.submit(async () => apply(await sendEmail(account!.id, email)))

  const handleCode = (code: string) => action.submit(async () => {
    try {
      apply(await sendCode(account!.id, code))
    } catch (e) {
      // An email with no Beeper account sends sign-in back to the email step.
      const current = await getAccount(account!.id)
      setAccount(current)
      setStep(current.step)
      throw e
    }
  })

  const steps = Object.keys(STEPS) as Step[]
  return (
    <Modal title={initial ? `Sign in to ${initial.name || initial.server_url}` : 'Add Beeper account'} onClose={onClose}>
      <div className="space-y-4">
        <Hint>Step {steps.indexOf(step) + 1} of {steps.length}: {STEPS[step]}</Hint>
        {action.error && <Notice tone="error">{action.error}</Notice>}
        {step === 'server' && <ServerStep busy={action.busy} onSubmit={handleCreate} onCancel={onClose} />}
        {step === 'email' && account && <EmailStep key={account.id} initial={account.email ?? ''} busy={action.busy} onSubmit={handleEmail} />}
        {step === 'code' && account && (
          <CodeStep email={account.email} busy={action.busy} onSubmit={handleCode}
            onChangeEmail={() => { action.setError(null); setStep('email') }} />
        )}
        {step === 'verify' && account && <VerifyStep account={account} verification={verification} onSetup={apply} />}
        {step === 'done' && account && <DoneStep account={account} onClose={onClose} />}
      </div>
    </Modal>
  )
}

const ServerStep = ({ busy, onSubmit, onCancel }: {
  busy: boolean
  onSubmit: (data: { server_url: string; name?: string; collect_by_default: boolean }) => void
  onCancel: () => void
}) => {
  const { listServers } = useBeeper()
  const [servers, setServers] = useState<BeeperServer[] | Error | null>(null)
  const [serverUrl, setServerUrl] = useState('')
  const [name, setName] = useState('')
  const [collect, setCollect] = useState(true)

  useEffect(() => {
    listServers()
      .then(list => {
        setServers(list)
        setServerUrl(list.find(s => !s.in_use)?.url ?? '')
      })
      .catch(e => setServers(e instanceof Error ? e : new Error(String(e))))
  }, [listServers])

  if (servers === null) return <Hint>Loading Beeper Servers...</Hint>
  if (servers instanceof Error) return <Notice tone="error">{servers.message}</Notice>
  if (servers.length === 0) {
    return <Notice tone="warning">No Beeper Server is set up. Ask an admin to run one and add its URL to BEEPER_SERVER_URLS.</Notice>
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit({ server_url: serverUrl, name: name.trim() || undefined, collect_by_default: collect })
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Beeper Server" hint="Each Beeper Server holds one Beeper account">
        <Select value={serverUrl} onChange={e => setServerUrl(e.target.value)} required>
          {servers.map(s => <option key={s.url} value={s.url} disabled={s.in_use}>{s.in_use ? `${s.url} (in use)` : s.url}</option>)}
        </Select>
      </Field>
      {!serverUrl && <Notice tone="warning">Every Beeper Server is in use. Ask an admin to run another one.</Notice>}
      <Field label="Name" hint="Shown on this account's card, e.g. Personal">
        <Input type="text" value={name} onChange={e => setName(e.target.value)} />
      </Field>
      <div>
        <Checkbox label="Collect everything" checked={collect} onChange={e => setCollect(e.target.checked)} />
        <Hint>{collect
          ? 'Every network and chat is collected as it appears; turn any of them off later.'
          : 'Nothing is collected until you turn a network or chat on.'}</Hint>
      </div>
      <FormActions>
        <Button onClick={onCancel} disabled={busy}>Cancel</Button>
        <Button variant="primary" type="submit" disabled={busy || !serverUrl}>{busy ? 'Adding...' : 'Continue'}</Button>
      </FormActions>
    </form>
  )
}

const EmailStep = ({ initial, busy, onSubmit }: { initial: string; busy: boolean; onSubmit: (email: string) => void }) => {
  const [email, setEmail] = useState(initial)
  return (
    <form onSubmit={e => { e.preventDefault(); onSubmit(email.trim()) }} className="space-y-4">
      <Field label="Beeper account email" hint="Beeper emails a sign-in code here. Memory never creates a Beeper account.">
        <Input type="email" autoFocus autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} required />
      </Field>
      <FormActions>
        <Button variant="primary" type="submit" disabled={busy}>{busy ? 'Sending...' : 'Send code'}</Button>
      </FormActions>
    </form>
  )
}

const CodeStep = ({ email, busy, onSubmit, onChangeEmail }: {
  email: string | null
  busy: boolean
  onSubmit: (code: string) => void
  onChangeEmail: () => void
}) => {
  const [code, setCode] = useState('')
  return (
    <form onSubmit={e => { e.preventDefault(); onSubmit(code.trim()) }} className="space-y-4">
      <Field label="Code" hint={`The code Beeper emailed to ${email ?? 'you'}`}>
        <Input type="text" autoFocus autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} required />
      </Field>
      <FormActions>
        <Button onClick={onChangeEmail} disabled={busy}>Send a new code</Button>
        <Button variant="primary" type="submit" disabled={busy}>{busy ? 'Signing in...' : 'Sign in'}</Button>
      </FormActions>
    </form>
  )
}

const VerifyStep = ({ account, verification, onSetup }: {
  account: BeeperAccount
  verification: BeeperVerification | null
  onSetup: (setup: SetupState) => void
}) => {
  const { getSetup, startVerification, verificationAction, sendRecoveryKey } = useBeeper()
  const [useKey, setUseKey] = useState(false)
  const [recoveryKey, setRecoveryKey] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [pollError, setPollError] = useState<string | null>(null)
  const action = useSubmit()

  // The other device answers in the Beeper app, so its moves only show up by asking the Server.
  useEffect(() => {
    const poll = () => getSetup(account.id)
      .then(setup => { setPollError(null); onSetup(setup) })
      .catch(e => setPollError(e instanceof Error ? e.message : String(e)))
    poll()
    const timer = setInterval(poll, POLL_MS)
    return () => clearInterval(timer)
    // onSetup only sets state, so a new identity each render needn't restart the poll.
  }, [account.id, getSetup])

  const act = (name: BeeperVerificationAction) => action.submit(async () => {
    onSetup(await verificationAction(account.id, verification!.id, name))
    if (name === 'sas-confirm') setConfirmed(true)
  })
  const start = () => action.submit(async () => {
    setConfirmed(false)
    onSetup(await startVerification(account.id))
  })
  const submitKey = (e: React.FormEvent) => {
    e.preventDefault()
    action.submit(async () => {
      onSetup(await sendRecoveryKey(account.id, recoveryKey.trim()))
      setRecoveryKey('')
    })
  }

  const errors = <>
    {action.error && <Notice tone="error">{action.error}</Notice>}
    {pollError && <Notice tone="warning">Couldn't check the verification: {pollError}</Notice>}
  </>

  if (useKey) {
    return (
      <form onSubmit={submitKey} className="space-y-4">
        {errors}
        <Field label="Recovery key" hint="Beeper showed it when you set up encryption; find it under Settings → Security in the Beeper app">
          <Input type="password" autoFocus autoComplete="off" spellCheck={false} value={recoveryKey} onChange={e => setRecoveryKey(e.target.value)} required />
        </Field>
        <FormActions>
          <Button onClick={() => { action.setError(null); setUseKey(false) }} disabled={action.busy}>Use emoji instead</Button>
          <Button variant="primary" type="submit" disabled={action.busy}>{action.busy ? 'Verifying...' : 'Verify'}</Button>
        </FormActions>
      </form>
    )
  }

  const active = verification && !FINISHED.includes(verification.state) && !verification.error_code
  const keyLink = <Button variant="link" onClick={() => setUseKey(true)}>Use recovery key instead</Button>
  const otherDevice = verification?.other_device_name || 'your other Beeper device'

  if (!active) {
    return (
      <div className="space-y-4">
        {errors}
        {verification?.state === 'cancelled' && <Notice tone="warning">The verification was cancelled. Start it again to approve this device.</Notice>}
        {verification?.error_code && <Notice tone="error">The verification failed ({verification.error_code}). Start it again to approve this device.</Notice>}
        <p className="text-sm text-slate-700">
          Signed in{account.email ? ` as ${account.email}` : ''}. Approve this device from a phone or computer where you use Beeper, so memory can read your encrypted chats.
        </p>
        <FormActions className="items-center justify-between">
          {keyLink}
          <Button variant="primary" onClick={start} disabled={action.busy}>{action.busy ? 'Starting...' : 'Start verification'}</Button>
        </FormActions>
      </div>
    )
  }

  if (verification.sas_emojis) {
    const emojis = verification.sas_emojis.split(/\s+/).filter(Boolean)
    return (
      <div className="space-y-4">
        {errors}
        <ul aria-label="Verification emoji" className="flex flex-wrap justify-center gap-4 py-2">
          {emojis.map((emoji, i) => <li key={i} className="text-5xl leading-none">{emoji}</li>)}
        </ul>
        <p className="text-base font-medium text-slate-800 text-center">
          Check these match the emoji in your Beeper app, then tap “They match” there and here.
        </p>
        {confirmed && <Notice tone="info">Waiting for {otherDevice} to confirm too...</Notice>}
        <FormActions>
          <Button onClick={() => act('cancel')} disabled={action.busy}>Cancel</Button>
          {can(verification, 'sas.confirm') && <Button variant="primary" onClick={() => act('sas-confirm')} disabled={action.busy || confirmed}>They match</Button>}
        </FormActions>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {errors}
      {can(verification, 'accept')
        ? <p className="text-sm text-slate-700">{otherDevice} asked to verify this device. Accept to compare emoji.</p>
        : can(verification, 'sas.start')
          ? <p className="text-sm text-slate-700">{otherDevice} accepted. Show the emoji to compare them.</p>
          : <p className="text-sm text-slate-700" role="status">Open Beeper on {otherDevice} and accept the verification request. This updates by itself.</p>}
      <FormActions className="items-center justify-between">
        {keyLink}
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => act('cancel')} disabled={action.busy}>Cancel</Button>
          {can(verification, 'accept') && <Button variant="primary" onClick={() => act('accept')} disabled={action.busy}>Accept</Button>}
          {can(verification, 'sas.start') && <Button variant="primary" onClick={() => act('sas-start')} disabled={action.busy}>Show emoji</Button>}
        </div>
      </FormActions>
    </div>
  )
}

const DoneStep = ({ account, onClose }: { account: BeeperAccount; onClose: () => void }) => {
  const { refreshNetworks } = useBeeper()
  const [networks, setNetworks] = useState<BeeperNetwork[] | Error | null>(null)

  const load = () => {
    setNetworks(null)
    refreshNetworks(account.id)
      .then(setNetworks)
      .catch(e => setNetworks(e instanceof Error ? e : new Error(String(e))))
  }
  useEffect(load, [account.id, refreshNetworks])

  return (
    <div className="space-y-4">
      <Notice tone="success">Signed in and verified{account.email ? ` as ${account.email}` : ''}.</Notice>
      {networks === null && <Hint>Looking for networks...</Hint>}
      {networks instanceof Error && <Notice tone="error" onRetry={load}>Couldn't list networks: {networks.message}</Notice>}
      {Array.isArray(networks) && (networks.length === 0
        ? <Hint>No networks yet. Connect one in the Beeper app, then refresh networks on the account's card.</Hint>
        : (
          <List>
            {networks.map(n => <Row key={n.id} title={n.display_name || n.network} meta={[n.network, n.collect_messages ? 'Collecting' : 'Paused']} />)}
          </List>
        ))}
      <CloudNote />
      <FormActions><Button variant="primary" onClick={onClose}>Done</Button></FormActions>
    </div>
  )
}
