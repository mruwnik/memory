import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../hooks/useAuth'
import {
  useIdentity, type AccountKind, type DiscordLinkCode, type Identity, type IdentityPerson, type IdentitySuggestion, type LinkedAccount, type NotifyChannel, type ReasonKind,
} from '../../hooks/useIdentity'
import { formatUserTime } from '@/utils/timezones'
import {
  Badge, Button, Card, ConfirmButton, CopyButton, DetailList, ErrorState, LoadingState, Notice, RefreshButton, Row, SubList, useActionNotice,
} from '@/components/shared/ui'

const KINDS: { kind: AccountKind; label: string }[] = [
  { kind: 'discord', label: 'Discord' },
  { kind: 'slack', label: 'Slack' },
  { kind: 'github', label: 'GitHub' },
  { kind: 'email', label: 'Email' },
]
const KIND_LABEL = Object.fromEntries(KINDS.map(({ kind, label }) => [kind, label])) as Record<AccountKind, string>

const REASON_LABEL: Record<ReasonKind, string> = {
  slack: 'Slack account',
  github: 'GitHub account',
  email: 'email address',
}

const CHANNEL_LABEL: Record<NotifyChannel, string> = { discord: 'Discord DM', slack: 'Slack DM', email: 'email' }

const personName = (p: IdentityPerson) => p.display_name === p.identifier ? p.display_name : `${p.display_name} (${p.identifier})`

// Slack ids are "<team id>:<user id>"; the user id is the part people recognise.
const shownId = (kind: AccountKind, id: string) => kind === 'slack' ? id.slice(id.indexOf(':') + 1) : id

/** A reason names the account by id (a GitHub one by its numeric id): show the label the user's own account carries. */
const reasonText = (s: IdentitySuggestion, accounts: LinkedAccount[]) =>
  s.reasons.map(r => {
    const label = accounts.find(a => a.kind === r.kind && a.id === r.value)?.label ?? shownId(r.kind, r.value)
    return `your ${REASON_LABEL[r.kind]} ${label}`
  }).join(' and ')

const accountNote = (a: LinkedAccount) =>
  !a.on_person ? 'On another person record: ask an admin to merge' : !a.removable && 'Connected account: manage it in Sources'

const unlinkConfirm = (a: LinkedAccount) => a.on_person
  ? `Unlink ${KIND_LABEL[a.kind]} account ${a.label} from your person record? Alerts stop going to it and what it writes is no longer tied to you.`
  : `Undo your verification of ${KIND_LABEL[a.kind]} account ${a.label}? It stays on the other person record.`

const verifiedDiscordIds = (identity: Identity | null) =>
  new Set((identity?.accounts ?? []).filter(a => a.kind === 'discord' && a.verified).map(a => a.id))

const minutesLeft = (iso: string) => Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 60000))

/** Where a channel's problem gets fixed: Sources. A missing Discord account's problem already names this card's button. */
const ProblemFix = ({ channel, hasDiscord }: { channel: NotifyChannel; hasDiscord: boolean }) => {
  if (channel === 'discord' && !hasDiscord) return null
  const tab = channel === 'discord' ? 'discord' : channel
  const text = channel === 'discord' ? 'Add a Discord bot' : channel === 'slack' ? 'Connect Slack' : 'Add an email account'
  return <Link to={`/sources?tab=${tab}`} className="text-primary underline hover:no-underline">{text}</Link>
}

const AlertRouting = ({ identity }: { identity: Identity }) => {
  const { order, chosen, problems } = identity.notify
  const route = order.map(c => CHANNEL_LABEL[c]).join(', else ')
  const hasDiscord = identity.accounts.some(a => a.kind === 'discord')
  const broken = order.filter(c => problems[c])
  return (
    <SubList title="Where alerts go">
      <div className="space-y-2 text-sm text-slate-700">
        <p>Alerts try {route}.</p>
        <p className="flex flex-wrap items-center gap-2">
          {chosen
            ? <>Right now they go by <Badge tone="success">{CHANNEL_LABEL[chosen]}</Badge></>
            : <Badge tone="danger">No channel can reach you</Badge>}
        </p>
        {broken.length > 0 && (
          <ul className="space-y-1">
            {broken.map(c => (
              <li key={c}>
                <span className="font-medium first-letter:uppercase inline-block">{CHANNEL_LABEL[c]}:</span> {problems[c]}. <ProblemFix channel={c} hasDiscord={hasDiscord} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </SubList>
  )
}

const DiscordCode = ({ code, onRefresh, onNewCode, busy }: { code: DiscordLinkCode; onRefresh: () => void; onNewCode: () => void; busy: boolean }) => {
  const [expired, setExpired] = useState(() => minutesLeft(code.expires_at) === 0)
  useEffect(() => {
    const ms = new Date(code.expires_at).getTime() - Date.now()
    if (ms <= 0) return
    const timer = setTimeout(() => setExpired(true), ms)
    return () => clearTimeout(timer)
  }, [code.expires_at])

  const message = `link ${code.code}`
  if (expired) {
    return (
      <Notice tone="warning">
        The code {code.code} has expired. <Button size="sm" onClick={onNewCode} disabled={busy}>Get a new code</Button>
      </Notice>
    )
  }
  if (!code.bots.length) {
    return <Notice tone="warning">There is no Discord bot you can DM, so the code can't be used. A bot has to be added on the Discord tab of Sources first.</Notice>
  }
  const bots = code.bots.length === 1 ? code.bots[0] : `one of: ${code.bots.join(', ')}`
  return (
    <Notice tone="info">
      <div className="space-y-2">
        <p>
          DM <code className="bg-white/70 px-1.5 py-0.5 rounded font-mono">{message}</code> to {bots} within {minutesLeft(code.expires_at)} minutes
          (the code expires at {formatUserTime(code.expires_at)}).
        </p>
        <p>
          The bot answers with your Memory login. Check it is yours, then reply{' '}
          <code className="bg-white/70 px-1.5 py-0.5 rounded font-mono">confirm</code> within 5 minutes to finish.
        </p>
        <div className="flex flex-wrap gap-2">
          {/* The button that asked for the code is gone, so focus lands on the next step instead of the page. */}
          <CopyButton text={message} label="Copy message" autoFocus />
          <Button size="sm" variant="primary" onClick={onRefresh} disabled={busy}>I've confirmed: check again</Button>
        </div>
      </div>
    </Notice>
  )
}

const IdentityCard = () => {
  const { getIdentity, claimPerson, unlinkAccount, discordLinkCode } = useIdentity()
  const { hasScope } = useAuth()
  const { run, notice } = useActionNotice()
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [code, setCode] = useState<DiscordLinkCode | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setIdentity(await getIdentity())
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load your identity')
    } finally {
      setLoading(false)
    }
  }, [getIdentity])

  useEffect(() => { load() }, [load])

  const act = async (action: () => Promise<unknown>, success?: string) => {
    setBusy(true)
    const ok = await run(action, success)
    setBusy(false)
    return ok
  }

  const newCode = () => act(async () => setCode(await discordLinkCode()))

  const checkAgain = () => act(async () => {
    const before = verifiedDiscordIds(identity)
    const next = await getIdentity()
    setIdentity(next)
    if ([...verifiedDiscordIds(next)].some(id => !before.has(id))) {
      setCode(null)
      return
    }
    throw new Error('No Discord account has been verified yet. Reply confirm to the bot once it answers your DM, then check again.')
  }, 'Discord account verified')

  if (!identity) {
    return (
      <Card title="Your identity">
        {loading ? <LoadingState message="Loading your accounts..." /> : <ErrorState message={loadError ?? 'Failed to load your identity'} onRetry={load} />}
      </Card>
    )
  }

  const { person, accounts, suggestions } = identity
  const claimVerb = person ? 'Merge' : 'Claim'

  return (
    <Card title="Your identity" region
      description="The accounts that are you. Alerts reach you through them, and what they write is linked to you."
      actions={<RefreshButton onClick={load} loading={loading} />}>
      <div className="space-y-4">
        {notice}

        <DetailList items={[
          ['Person record', person
            ? hasScope('people')
              ? <Link to={`/people?search=${encodeURIComponent(person.identifier)}`} className="text-primary underline hover:no-underline">{personName(person)}</Link>
              : personName(person)
            : <span className="text-slate-600">{suggestions.length ? 'None yet: claim a suggested record below.' : 'None yet. Whoever runs this server can link your person record to your login.'}</span>],
        ]} />

        {suggestions.length > 0 && (
          <SubList title="Records that look like you">
            {suggestions.map(s => (
              <Row key={s.person.id} title={`Person ${personName(s.person)}`} meta={[`has ${reasonText(s, accounts)}`]}
                actions={
                  <ConfirmButton variant="primary" disabled={busy}
                    confirm={person
                      ? `Merge ${personName(s.person)} into your record, ${personName(person)}? Its accounts, aliases and notes move to yours and the other record is deleted. This can't be undone.`
                      : `Claim ${personName(s.person)} as your person record? Its accounts become yours, so alerts and their content are tied to you.`}
                    onConfirm={() => act(async () => setIdentity(await claimPerson(s.person.id)),
                      person ? `Merged ${s.person.display_name} into your record` : `${s.person.display_name} is now your person record`)}>
                    {claimVerb}
                  </ConfirmButton>
                } />
            ))}
          </SubList>
        )}

        <AlertRouting identity={identity} />

        {KINDS.map(({ kind, label }) => {
          const linked = accounts.filter(a => a.kind === kind)
          return (
            <SubList key={kind} title={label} empty={`No ${label} account linked.`}
              actions={kind === 'discord' && !code && <Button size="sm" onClick={newCode} disabled={busy}>Link a Discord account</Button>}
              footer={kind === 'discord' && code && <DiscordCode code={code} busy={busy} onRefresh={checkAgain} onNewCode={newCode} />}>
              {linked.map(a => (
                <Row key={`${a.kind}:${a.id}`} title={a.label}
                  badges={a.verified ? <Badge tone="success">verified</Badge> : <Badge>unverified</Badge>}
                  meta={[shownId(a.kind, a.id) !== a.label && shownId(a.kind, a.id), accountNote(a)]}
                  actions={a.removable && (
                    <ConfirmButton disabled={busy} confirm={unlinkConfirm(a)}
                      onConfirm={() => act(async () => setIdentity(await unlinkAccount(a.kind, a.id)), `Unlinked ${a.label}`)}>
                      Unlink
                    </ConfirmButton>
                  )} />
              ))}
            </SubList>
          )
        })}
      </div>
    </Card>
  )
}

export default IdentityCard
