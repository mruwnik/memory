import { useCallback, useEffect, useState, type ComponentType } from 'react'
import { useSetup, type SourceKind, type SetupStatus } from '@/hooks/useSetup'
import { AccountsPanel } from '@/components/sources/panels/AccountsPanel'
import { CalendarPanel } from '@/components/sources/panels/CalendarPanel'
import { DiscordPanel } from '@/components/sources/panels/DiscordPanel'
import { EmailPanel } from '@/components/sources/panels/EmailPanel'
import { FeedsPanel } from '@/components/sources/panels/FeedsPanel'
import { GitHubPanel } from '@/components/sources/panels/GitHubPanel'
import { GoogleDrivePanel } from '@/components/sources/panels/GoogleDrivePanel'
import { SlackPanel } from '@/components/sources/panels/SlackPanel'
import { TranscriptsPanel } from '@/components/sources/panels/TranscriptsPanel'
import { Badge, Button, ButtonLink, List, LoadingState, Modal, Notice, Row, NewTabMark } from '@/components/shared/ui'

interface Source {
  kind: SourceKind
  title: string
  description: string
  /** The Sources page panels that connect it, shown in order. */
  panels?: ComponentType[]
}

const GoogleAccounts = () => <AccountsPanel provider="google" />
const GithubAccounts = () => <AccountsPanel provider="github" />

const SOURCES: Source[] = [
  { kind: 'email', title: 'Email', description: 'IMAP or Gmail mailboxes.', panels: [EmailPanel] },
  { kind: 'google', title: 'Google Drive and Calendar', description: 'Connect a Google account, then pick Drive folders and calendars.', panels: [GoogleAccounts, GoogleDrivePanel, CalendarPanel] },
  { kind: 'github', title: 'GitHub', description: 'Add a GitHub account, then the repos and projects to follow.', panels: [GithubAccounts, GitHubPanel] },
  { kind: 'slack', title: 'Slack', description: 'Workspaces and channels, through a Slack app you create.', panels: [SlackPanel] },
  { kind: 'discord', title: 'Discord', description: 'Servers and channels a Discord bot can read.', panels: [DiscordPanel] },
  { kind: 'feeds', title: 'RSS feeds', description: 'Blogs and news feeds.', panels: [FeedsPanel] },
  { kind: 'notes', title: 'Notes and uploads', description: 'Write notes here, or ask your AI client to save a note or upload a file.' },
  { kind: 'transcripts', title: 'Meeting transcripts', description: 'Recordings from a transcript service.', panels: [TranscriptsPanel] },
]

export const SourcesStep = () => {
  const { getStatus } = useSetup()
  const [counts, setCounts] = useState<SetupStatus['sources'] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [open, setOpen] = useState<Source | null>(null)

  const load = useCallback(() => {
    setLoadError(null)
    getStatus().then(s => setCounts(s.sources), e => setLoadError(e.message))
  }, [getStatus])
  useEffect(load, [load])

  const close = () => {
    setOpen(null)
    load()
  }

  if (loadError) return <Notice tone="error" onRetry={load}>{loadError}</Notice>
  if (!counts) return <LoadingState message="Loading sources..." />
  return (
    <>
      <List aria-label="Sources">
        {SOURCES.map(source => (
          <Row key={source.kind} title={source.title}
            badges={counts[source.kind] > 0 && <Badge tone="success">Connected</Badge>}
            meta={[source.description]}
            actions={source.panels
              ? <Button size="sm" aria-label={`Set up ${source.title}`} onClick={() => setOpen(source)}>Set up</Button>
              : <ButtonLink size="sm" to="/ui/notes" target="_blank" rel="noopener noreferrer">Open notes<NewTabMark /></ButtonLink>} />
        ))}
      </List>
      {open?.panels && (
        <Modal title={open.title} onClose={close} size="xl">
          <div className="space-y-6">
            {open.panels.map((Panel, i) => <Panel key={i} />)}
          </div>
        </Modal>
      )}
    </>
  )
}
