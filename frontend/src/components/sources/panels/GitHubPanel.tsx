import { useState, useEffect, useCallback } from 'react'
import { useSources, GithubAccount, GithubContentMode, GithubProject, GithubRepo, GithubWebhookStatus, AvailableRepo, AvailableProject } from '@/hooks/useSources'
import { TagsInput, PauseButton, PUSH_CAUSES, PushBadge, PushMissedBadge, type PushSetup, ScheduleButton, SyncScheduleFields, StatusBadge, SyncButton, SyncFailingBadge, SyncFailure, formatRelativeTime } from '../shared'
import { Badge, Button, Checkbox, ConfirmButton, CopyButton, DetailList, Disclosure, EmptyState, ErrorState, Field, FormActions, Input, List, LoadingState, Modal, Notice, Row, RowError, Section, Select, SubList, cx, useActionNotice, Fieldset, NewTabLink } from '@/components/shared/ui'
import { useDebounce } from '@/hooks/useDebounce'
import { useSourcesContext } from '../context'
import { llmKeyLabel } from '../LLMKeyField'

export const GitHubPanel = () => {
  const {
    listGithubAccounts,
    addGithubRepo, updateGithubRepo, deleteGithubRepo, syncGithubRepo,
    listAccountProjects, addGithubProject, deleteGithubProject, installGithubWebhooks
  } = useSources()
  const { userId, refreshCounts } = useSourcesContext()
  const [accounts, setAccounts] = useState<GithubAccount[]>([])
  const [accountProjects, setAccountProjects] = useState<Record<number, GithubProject[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingRepoTo, setAddingRepoTo] = useState<number | null>(null)
  const [addingProjectTo, setAddingProjectTo] = useState<number | null>(null)
  const [webhookFor, setWebhookFor] = useState<number | null>(null)
  const webhookAccount = accounts.find(a => a.id === webhookFor)
  const { run, notice } = useActionNotice()

  const loadData = useCallback(async () => {
    try {
      const accountsData = await listGithubAccounts(userId)
      setAccounts(accountsData)

      // Load projects for each account
      const projectsMap: Record<number, GithubProject[]> = {}
      await Promise.all(
        accountsData.map(async (account) => {
          try {
            projectsMap[account.id] = await listAccountProjects(account.id)
          } catch {
            projectsMap[account.id] = []
          }
        })
      )
      setAccountProjects(projectsMap)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
      refreshCounts()
    }
  }, [listGithubAccounts, listAccountProjects, userId, refreshCounts])

  useEffect(() => { loadData() }, [loadData])

  // Note: These handlers don't call loadData() - GitHubRepoForm handles batch
  // operations and calls onComplete when done, which triggers a single reload
  const handleAddRepo = async (accountId: number, data: any) => {
    await addGithubRepo(accountId, data)
  }

  const handleDeleteRepo = async (accountId: number, repoId: number) => {
    await deleteGithubRepo(accountId, repoId)
  }

  const handleRepoFormComplete = () => {
    setAddingRepoTo(null)
    loadData()
  }

  const handleRepoFormCancel = () => {
    setAddingRepoTo(null)
  }

  // Refreshes the tracked repos without the panel's loading state, which would unmount the open repo form.
  const refreshAccounts = async () => setAccounts(await listGithubAccounts(userId))

  const handleToggleRepoActive = (accountId: number, repoId: number, active: boolean) => run(async () => {
    await updateGithubRepo(accountId, repoId, { active: !active })
    loadData()
  })

  const handleSetContentMode = (accountId: number, repoId: number, content_mode: GithubContentMode) => run(async () => {
    await updateGithubRepo(accountId, repoId, { content_mode })
    loadData()
  })

  const handleSyncRepo = (accountId: number, repoId: number) => run(async () => {
    await syncGithubRepo(accountId, repoId)
    loadData()
  }, 'Sync queued')

  const handleReinstallHooks = (accountId: number) => run(async () => {
    await installGithubWebhooks(accountId)
    loadData()
  }, 'Reinstalling. Push is marked healthy again once an event arrives.')

  const handleAddProject = async (accountId: number, data: { owner: string; project_number: number; is_org: boolean }) => {
    await addGithubProject(accountId, data)
    setAddingProjectTo(null)
    loadData()
  }

  const handleDeleteProject = (projectId: number) => run(async () => {
    await deleteGithubProject(projectId)
    loadData()
  })

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <Section title="GitHub">
      {notice}
      {accounts.length === 0 ? (
        <EmptyState message="No GitHub accounts yet. Add a GitHub account first." />
      ) : (
        <List separate>
          {accounts.map(account => {
            const projects = accountProjects[account.id] || []
            const missing = account.repos.filter(r => r.push_missing_since)
            return (
              <Row
                key={account.id}
                title={account.name}
                muted={!account.active}
                badges={<><StatusBadge active={account.active} /><AccountPushBadge account={account} /></>}
                meta={[
                  account.auth_type === 'pat' ? 'Personal access token' : 'GitHub App',
                  account.verified_login ? `verified as ${account.verified_login}` : 'unverified',
                  `LLM: ${llmKeyLabel(account.llm_key)}`,
                ]}
                actions={<>
                  {missing.length > 0 && <Button size="sm" onClick={() => handleReinstallHooks(account.id)}>Reinstall webhooks</Button>}
                  <Button size="sm" onClick={() => setWebhookFor(account.id)}>Webhook</Button>
                </>}
              >
                {missing.length > 0 && (
                  <RowError>
                    Polling found changes to {missing.map(r => r.repo_path).join(', ')} that {missing.length === 1 ? 'its webhook' : 'their webhooks'} never
                    delivered, so they arrived late.
                  </RowError>
                )}
                <SubList
                  title={`Repositories (${account.repos.length})`}
                  actions={<Button size="sm" onClick={() => setAddingRepoTo(account.id)} disabled={!account.active}>Add repo</Button>}
                  empty="No repositories tracked"
                >
                  {[...account.repos].sort((a, b) => a.repo_path.localeCompare(b.repo_path)).map(repo => (
                    <Row
                      key={repo.id}
                      title={repo.repo_path}
                      muted={!repo.active}
                      badges={<>
                        <StatusBadge active={repo.active} />
                        {repo.track_issues && <Badge tone="info">Issues</Badge>}
                        {repo.track_prs && <Badge tone="primary">PRs</Badge>}
                        {repo.track_comments && <Badge tone="success">Comments</Badge>}
                        {repo.content_mode === 'metadata' && <Badge title={CONTENT_MODE_HINT.metadata}>Metadata only</Badge>}
                        <SyncFailingBadge status={repo} />
                        <PushMissedBadge status={repo} causes={PUSH_CAUSES.github} />
                      </>}
                      meta={[`Last sync: ${formatRelativeTime(repo.last_sync_at)}`, repo.push_stale && 'No recent push events']}
                      actions={<>
                        <SyncButton onSync={() => handleSyncRepo(account.id, repo.id)} disabled={!repo.active || !account.active} label="Sync" />
                        <ScheduleButton
                          schedule={{ check_interval: repo.check_interval }}
                          onSave={async ({ check_interval }) => { await updateGithubRepo(account.id, repo.id, { check_interval }); loadData() }}
                        />
                        <ContentModeButton repo={repo} onSwitch={mode => handleSetContentMode(account.id, repo.id, mode)} />
                        <PauseButton active={repo.active} onClick={() => handleToggleRepoActive(account.id, repo.id, repo.active)} />
                        <ConfirmButton confirm={`Stop tracking ${repo.repo_path}?`} onConfirm={() => run(async () => { await handleDeleteRepo(account.id, repo.id); loadData() })}>Remove</ConfirmButton>
                      </>}
                    >
                      <SyncFailure status={repo} />
                    </Row>
                  ))}
                </SubList>
                <SubList
                  title={`Projects (${projects.length})`}
                  actions={<Button size="sm" onClick={() => setAddingProjectTo(account.id)} disabled={!account.active}>Add project</Button>}
                  empty="No projects tracked"
                >
                  {projects.map(project => (
                    <Row
                      key={project.id}
                      title={
                        <NewTabLink href={project.url} className="text-primary hover:underline">
                          {project.owner_login}/#{project.number} - {project.title}
                        </NewTabLink>
                      }
                      badges={<>
                        <Badge>{project.items_total_count} items</Badge>
                        {project.fields.slice(0, 3).map(field => <Badge key={field.id}>{field.name}</Badge>)}
                        {project.fields.length > 3 && <Badge>+{project.fields.length - 3}</Badge>}
                      </>}
                      meta={[`Last sync: ${formatRelativeTime(project.last_sync_at)}`]}
                      actions={<ConfirmButton confirm={`Stop tracking ${project.title}?`} onConfirm={() => handleDeleteProject(project.id)}>Remove</ConfirmButton>}
                    />
                  ))}
                </SubList>
              </Row>
            )
          })}
        </List>
      )}

      {addingRepoTo && (() => {
        const account = accounts.find(a => a.id === addingRepoTo)
        const existingRepos = account?.repos.map(r => r.repo_path) || []
        const repoIdMap = new Map(account?.repos.map(r => [r.repo_path, r.id]) || [])
        return (
          <GitHubRepoForm
            accountId={addingRepoTo}
            existingRepos={existingRepos}
            repoIdMap={repoIdMap}
            onAdd={(data) => handleAddRepo(addingRepoTo, data)}
            onRemove={(repoId) => handleDeleteRepo(addingRepoTo, repoId)}
            onComplete={handleRepoFormComplete}
            onPartialFailure={refreshAccounts}
            onCancel={handleRepoFormCancel}
          />
        )
      })()}

      {webhookAccount && (
        <WebhookModal account={webhookAccount} onRefresh={loadData} onClose={() => setWebhookFor(null)} />
      )}

      {addingProjectTo && (
        <GitHubProjectForm
          accountId={addingProjectTo}
          existingProjects={accountProjects[addingProjectTo] || []}
          onAdd={(data) => handleAddProject(addingProjectTo, data)}
          onCancel={() => setAddingProjectTo(null)}
        />
      )}
    </Section>
  )
}

const CONTENT_MODE_HINT: Record<GithubContentMode, string> = {
  full: 'Issue and PR text, comments and diffs are stored and indexed for search.',
  metadata: 'Get notified about changes without storing or indexing text or code.',
}

const CONTENT_SWITCH: Record<GithubContentMode, { label: string; confirm: (repo: string) => string }> = {
  metadata: {
    label: 'Stop storing content',
    confirm: repo => `Stop storing text and code for ${repo}? Later syncs keep only what change detection needs, and subscriptions still fire. Content stored earlier stays as it is.`,
  },
  full: {
    label: 'Store content',
    confirm: repo => `Store and index text and code for ${repo}? Later syncs store issue and PR text, comments and diffs. What is stored already stays as it is.`,
  },
}

/** Switches a repo to the other content mode; the change applies to later syncs only. */
const ContentModeButton = ({ repo, onSwitch }: { repo: GithubRepo; onSwitch: (mode: GithubContentMode) => unknown }) => {
  const next: GithubContentMode = repo.content_mode === 'metadata' ? 'full' : 'metadata'
  const { label, confirm } = CONTENT_SWITCH[next]
  return <ConfirmButton variant="secondary" confirm={confirm(repo.repo_path)} onConfirm={() => onSwitch(next)}>{label}</ConfirmButton>
}

const WEBHOOK_EVENTS = 'Pull requests, Pull request reviews, Pull request review comments, Issue comments, Issues, Check runs, Check suites, Statuses'

const HOOK_STATUS: Record<GithubWebhookStatus, [string, 'success' | 'warning' | 'danger']> = {
  installed: ['Repo webhook', 'success'],
  org_hook: ['Org webhook', 'success'],
  missing_permission: ['Missing permission', 'danger'],
  rejected: ['Rejected by GitHub', 'danger'],
  error: ['Failed', 'warning'],
}
const HOOK_OK: (GithubWebhookStatus | null)[] = ['installed', 'org_hook']
const HOOK_FAILED: (GithubWebhookStatus | null)[] = ['missing_permission', 'rejected', 'error']

const HookStatusBadge = ({ status }: { status: GithubWebhookStatus | null }) => {
  const [label, tone] = status ? HOOK_STATUS[status] : ['Not installed', 'neutral' as const]
  return <Badge tone={tone}>{label}</Badge>
}

const activeRepos = (account: GithubAccount) =>
  account.repos.filter(r => r.active).sort((a, b) => a.repo_path.localeCompare(b.repo_path))

const AccountPushBadge = ({ account }: { account: GithubAccount }) => {
  const repos = activeRepos(account)
  const ok = repos.filter(r => HOOK_OK.includes(r.webhook_status))
  const failed = repos.filter(r => HOOK_FAILED.includes(r.webhook_status))
  const missing = repos.filter(r => r.push_missing_since)
  const lastEvent = repos.map(r => r.last_event_at).filter(Boolean).sort().at(-1) ?? null
  // A hook added by hand, which Install hasn't found yet, still shows by its deliveries.
  const setup: PushSetup = !account.webhook_secret_hint ? 'none' : failed.length ? 'error' : ok.length || lastEvent ? 'ok' : 'none'
  const deliveries = {
    // The account's outage spans its repos': from the first repo's miss to the latest.
    push_missing_since: missing.map(r => r.push_missing_since!).sort()[0] ?? null,
    push_last_miss_at: missing.map(r => r.push_last_miss_at ?? '').sort().at(-1) || null,
    push_stale: account.push_stale,
    last_event_at: lastEvent,
  }
  const detail = failed.length ? `${failed.length} of ${repos.length} repos: ${HOOK_STATUS[failed[0].webhook_status!][0].toLowerCase()}.` : undefined
  return <PushBadge setup={setup} deliveries={deliveries} causes={PUSH_CAUSES.github} detail={detail} />
}

const PERMISSION_HELP = {
  app: "The GitHub App needs permission to manage webhooks: Organization permissions → Webhooks: Read and write for an org's repos (one org webhook), or Repository permissions → Webhooks: Read and write for a personal account's repos (one per repo). Set it in the App's settings → Permissions & events, accept the new permissions on the installation, then install again.",
  pat: "The token needs permission to manage webhooks. Classic token: admin:org_hook for an org's repos (one org webhook; the token's owner must be an org owner), or admin:repo_hook for a personal account's repos (one per repo). Fine-grained token: Organization permissions → Webhooks: Read and write for an org, or Repository permissions → Webhooks: Read and write for a personal account.",
}

type HookHelp = ['info' | 'warning', string]

const hookHelp = (account: GithubAccount): HookHelp | null => {
  const statuses = new Set(activeRepos(account).map(r => r.webhook_status))
  if (statuses.has('missing_permission')) return ['warning', PERMISSION_HELP[account.auth_type]]
  if (statuses.has('rejected')) return ['warning', 'GitHub rejected the payload URL. It must be reachable from the public internet.']
  return null
}

const listed = (names: string[]) => names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`

// Whether an owner is an org or a person is only known to GitHub, so the plan covers both.
const installPlan = (owners: string[]) => {
  const where = owners.length === 1 ? `on ${owners[0]}, if it's an org` : `on each of ${listed(owners)} that is an org`
  return `Adds (or updates) one webhook pointing at Memory ${where}, covering all its repos, and one on each repo owned by a personal account. Other webhooks are left alone. Safe to run again.`
}

// GitHub logins are case-insensitive, and the install groups them that way too.
const byOwner = (repos: GithubRepo[]): [string, GithubRepo[]][] => {
  const owners = new Map<string, GithubRepo[]>()
  repos.forEach(r => owners.set(r.owner.toLowerCase(), [...owners.get(r.owner.toLowerCase()) ?? [], r]))
  return [...owners.values()].map(owned => [owned[0].owner, owned])
}

const WebhookModal = ({ account, onRefresh, onClose }: { account: GithubAccount; onRefresh: () => Promise<void>; onClose: () => void }) => {
  const { generateGithubWebhookSecret, deleteGithubWebhookSecret, installGithubWebhooks } = useSources()
  const [secret, setSecret] = useState<string | null>(null)
  const { run, notice } = useActionNotice()
  const hint = account.webhook_secret_hint
  const repos = activeRepos(account)
  const owners = byOwner(repos)
  const plan = installPlan(owners.map(([owner]) => owner))
  const help = hookHelp(account)

  const install = () => run(async () => {
    await installGithubWebhooks(account.id)
    await onRefresh()
  }, 'Installing. Refresh in a few seconds to see the result.')

  const generate = () => run(async () => {
    const result = await generateGithubWebhookSecret(account.id)
    setSecret(result.secret)
    await onRefresh()
  })

  const remove = () => run(async () => {
    await deleteGithubWebhookSecret(account.id)
    setSecret(null)
    await onRefresh()
  })

  return (
    <Modal title={`Webhook for ${account.name}`} onClose={onClose}>
      <div className="space-y-4">
        {notice}
        {repos.length === 0
          ? <Notice tone="info">This account tracks no repos, so there's nothing to install yet. Add a repo first.</Notice>
          : <p className="text-sm text-slate-600">{plan} Changes then arrive within seconds; polling still fills any gaps.</p>}
        {repos.length > 0 && (
          <List>
            {owners.map(([owner, owned]) => owned.every(r => r.webhook_status === 'org_hook')
              ? <Row key={owner} compact title={`${owner} org`} badges={<HookStatusBadge status="org_hook" />}
                  meta={[`covers ${owned.length} tracked ${owned.length === 1 ? 'repo' : 'repos'}`, owned[0].webhook_checked_at && `checked ${formatRelativeTime(owned[0].webhook_checked_at)}`]} />
              : owned.map(repo => (
                <Row key={repo.id} compact title={repo.repo_path} badges={<HookStatusBadge status={repo.webhook_status} />}
                  meta={[repo.webhook_checked_at && `checked ${formatRelativeTime(repo.webhook_checked_at)}`]} />
              )))}
          </List>
        )}
        {help && <Notice tone={help[0]}>{help[1]}</Notice>}
        <Disclosure summary="Set it up by hand">
          <div className="space-y-3">
            <p className="text-sm text-slate-600">
              On the org, an owner opens Settings → Webhooks → Add webhook; one org webhook covers all its repos.
              Without org access, add one in each tracked repo's Settings → Webhooks instead.
              Use this payload URL, content type <code>application/json</code> and the secret, and send: {WEBHOOK_EVENTS}.
            </p>
            <DetailList items={[
              ['Payload URL', <span className="flex items-center gap-2"><code className="break-all">{account.webhook_url}</code><CopyButton text={account.webhook_url} /></span>],
              ['Secret', hint ? `Set, ending in …${hint}` : 'None yet. Install webhook creates one; generate it here only to add hooks by hand.'],
            ]} />
            {secret && (
              <Notice tone="success">
                <p>Copy the secret now. It won't be shown again.</p>
                <span className="flex items-center gap-2 mt-2">
                  <code className="break-all">{secret}</code><CopyButton text={secret} />
                </span>
              </Notice>
            )}
            {hint
              ? <ConfirmButton variant="secondary" size="md" confirm="Rotate the secret? Memory re-installs its hooks with it; hooks added by hand reject deliveries until updated." confirmLabel="Rotate" onConfirm={generate}>Rotate secret</ConfirmButton>
              : <Button onClick={generate}>Generate secret</Button>}
          </div>
        </Disclosure>
        <FormActions>
          {hint && <ConfirmButton size="md" confirm="Remove the secret? Memory deletes the org and repo hooks pointing at it, and deliveries will be rejected." confirmLabel="Remove" onConfirm={remove}>Remove secret</ConfirmButton>}
          <Button onClick={() => run(onRefresh)}>Refresh</Button>
          <Button variant="primary" onClick={install} disabled={repos.length === 0}
            title={repos.length === 0 ? 'No tracked repos: add one first.' : plan}>Install webhook</Button>
          <Button onClick={onClose}>Done</Button>
        </FormActions>
      </div>
    </Modal>
  )
}

interface GitHubProjectFormProps {
  accountId: number
  existingProjects: GithubProject[]
  onAdd: (data: { owner: string; project_number: number; is_org: boolean }) => Promise<void>
  onCancel: () => void
}

const GitHubProjectForm = ({ accountId, existingProjects, onAdd, onCancel }: GitHubProjectFormProps) => {
  const [owner, setOwner] = useState('')
  const [isOrg, setIsOrg] = useState(true)
  const [selectedProject, setSelectedProject] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Waits for a pause in typing so an org name fetches its projects once, not per keystroke.
  const debouncedOwner = useDebounce(owner.trim(), 400)

  // Track which projects are already added
  const existingProjectKeys = new Set(
    existingProjects.map(p => `${p.owner_login}:${p.number}`)
  )

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!owner.trim() || selectedProject === null) {
      setError('Please enter an owner and select a project')
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      await onAdd({
        owner: owner.trim(),
        project_number: selectedProject,
        is_org: isOrg,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add project')
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Add GitHub project" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Owner (org or username)">
          <Input
            type="text"
            value={owner}
            onChange={e => {
              setOwner(e.target.value)
              setSelectedProject(null) // Reset selection when owner changes
            }}
            placeholder="e.g., my-organization"
            required
          />
        </Field>

        <Fieldset legend="Owner type">
          <div className="flex gap-4">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name="ownerType"
                checked={isOrg}
                onChange={() => {
                  setIsOrg(true)
                  setSelectedProject(null)
                }}
                className="rounded-full"
              />
              Organization
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="radio"
                name="ownerType"
                checked={!isOrg}
                onChange={() => {
                  setIsOrg(false)
                  setSelectedProject(null)
                }}
                className="rounded-full"
              />
              User
            </label>
          </div>
        </Fieldset>

        {debouncedOwner && debouncedOwner === owner.trim() && (
          <Fieldset legend="Project">
            <ProjectSelector
              accountId={accountId}
              owner={debouncedOwner}
              isOrg={isOrg}
              existingProjectKeys={existingProjectKeys}
              selectedProject={selectedProject}
              onSelect={setSelectedProject}
            />
          </Fieldset>
        )}

        <FormActions>
          <Button type="button" onClick={onCancel} disabled={submitting}>Cancel</Button>
          <Button variant="primary"
            type="submit"
            disabled={submitting || selectedProject === null}
          >
            {submitting ? 'Adding...' : 'Add project'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

interface ProjectSelectorProps {
  accountId: number
  owner: string
  isOrg: boolean
  existingProjectKeys: Set<string>
  selectedProject: number | null
  onSelect: (projectNumber: number | null) => void
}

const ProjectSelector = ({
  accountId,
  owner,
  isOrg,
  existingProjectKeys,
  selectedProject,
  onSelect,
}: ProjectSelectorProps) => {
  const { listAvailableProjects } = useSources()
  const [projects, setProjects] = useState<AvailableProject[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  useEffect(() => {
    // A reply for an owner or type the user has since changed must not replace the newer list.
    let stale = false
    const loadProjects = async () => {
      setLoading(true)
      setError(null)
      onSelect(null) // Reset selection when loading new projects
      try {
        const available = await listAvailableProjects(accountId, owner, isOrg, false)
        if (stale) return
        setProjects([...available].sort((a, b) => a.title.localeCompare(b.title)))
      } catch (e) {
        if (!stale) setError(e instanceof Error ? e.message : 'Failed to load projects')
      } finally {
        if (!stale) setLoading(false)
      }
    }
    loadProjects()
    return () => { stale = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onSelect is stable in behavior
    // but defined inline by parent; including it would cause infinite re-fetches
  }, [accountId, owner, isOrg, listAvailableProjects])

  const filteredProjects = projects.filter(project =>
    project.title.toLowerCase().includes(search.toLowerCase()) ||
    (project.short_description?.toLowerCase().includes(search.toLowerCase()) ?? false)
  )

  if (loading) return <LoadingState message="Loading projects..." />
  if (error) return <Notice tone="error">{error}</Notice>
  if (projects.length === 0) return <p className="text-sm text-slate-500 py-4">No projects found for {owner}</p>

  return (
    <div className="space-y-2">
      <Input
        type="search"
        aria-label="Filter projects"
        placeholder="Filter projects..."
        value={search}
        onChange={e => setSearch(e.target.value)}
      />
      <div className="max-h-60 overflow-y-auto border border-slate-200 rounded-lg">
        {filteredProjects.length === 0 ? (
          <div className="text-sm text-slate-500 p-4 text-center">
            {search ? 'No matching projects' : 'No projects available'}
          </div>
        ) : (
          filteredProjects.map(project => {
            const projectKey = `${owner}:${project.number}`
            const alreadyAdded = existingProjectKeys.has(projectKey)
            return (
              <label
                key={project.number}
                className={cx(
                  'flex items-start gap-3 p-3 border-b border-slate-100 last:border-b-0 cursor-pointer hover:bg-slate-50',
                  alreadyAdded && 'opacity-50 cursor-not-allowed'
                )}
              >
                <input
                  type="radio"
                  name="project-select"
                  checked={selectedProject === project.number}
                  onChange={() => onSelect(project.number)}
                  disabled={alreadyAdded}
                  className="mt-0.5 rounded-full"
                />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-slate-800">
                    #{project.number} - {project.title}
                    {alreadyAdded && <span className="ml-2 text-xs text-slate-500">(already added)</span>}
                    {project.closed && <span className="ml-2 text-xs text-slate-500">(closed)</span>}
                  </div>
                  <div className="text-sm text-slate-500">
                    {project.items_total_count} items
                    {project.short_description && ` • ${project.short_description}`}
                  </div>
                </div>
              </label>
            )
          })
        )}
      </div>
    </div>
  )
}

// RepoSelector - scrollable checkbox list showing all repos with monitoring status
interface RepoSelectorProps {
  accountId: number
  monitoredRepos: Set<string> // repos currently being monitored (full_name format)
  onChange: (monitored: Set<string>) => void
}

const RepoSelector = ({ accountId, monitoredRepos, onChange }: RepoSelectorProps) => {
  const { listAvailableRepos } = useSources()
  const [repos, setRepos] = useState<AvailableRepo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [refreshCount, setRefreshCount] = useState(0)

  useEffect(() => {
    const loadRepos = async () => {
      setLoading(true)
      setError(null)
      try {
        const available = await listAvailableRepos(accountId, refreshCount > 0)
        // Sort by full_name
        available.sort((a, b) => a.full_name.localeCompare(b.full_name))
        setRepos(available)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load repositories')
      } finally {
        setLoading(false)
      }
    }
    loadRepos()
  }, [accountId, listAvailableRepos, refreshCount])

  const filteredRepos = repos.filter(repo =>
    repo.full_name.toLowerCase().includes(search.toLowerCase()) ||
    (repo.description?.toLowerCase().includes(search.toLowerCase()) ?? false)
  )

  const toggleRepo = (fullName: string) => {
    const newMonitored = new Set(monitoredRepos)
    if (newMonitored.has(fullName)) {
      newMonitored.delete(fullName)
    } else {
      newMonitored.add(fullName)
    }
    onChange(newMonitored)
  }

  const selectAll = () => {
    const newMonitored = new Set(monitoredRepos)
    filteredRepos.forEach(repo => newMonitored.add(repo.full_name))
    onChange(newMonitored)
  }

  const selectNone = () => {
    const newMonitored = new Set(monitoredRepos)
    filteredRepos.forEach(repo => newMonitored.delete(repo.full_name))
    onChange(newMonitored)
  }

  if (loading) return <LoadingState message="Loading repositories... This may take a moment for large accounts." />
  if (error) return <>
    <Notice tone="error">{error}</Notice>
    <Button type="button" onClick={() => setRefreshCount(n => n + 1)}>Refresh from GitHub</Button>
  </>

  return (
    <div className="space-y-2">
      <Button type="button" onClick={() => setRefreshCount(n => n + 1)}>Refresh from GitHub</Button>
      <Input
        type="search"
        aria-label="Filter repositories"
        placeholder="Filter repositories..."
        value={search}
        onChange={e => setSearch(e.target.value)}
      />
      <div className="flex items-center gap-2 text-sm">
        <Button variant="link" type="button" onClick={selectAll}>All</Button>
        <Button variant="link" type="button" onClick={selectNone}>None</Button>
        <span className="text-slate-500 ml-auto">
          {monitoredRepos.size} monitored of {repos.length}
        </span>
      </div>
      <div className="max-h-60 overflow-y-auto border border-slate-200 rounded-lg">
        {filteredRepos.length === 0 ? (
          <div className="text-sm text-slate-500 p-4 text-center">
            {search ? 'No matching repositories' : 'No repositories available'}
          </div>
        ) : (
          filteredRepos.map(repo => (
            <label key={repo.full_name} className="flex items-start gap-3 p-3 border-b border-slate-100 last:border-b-0 cursor-pointer hover:bg-slate-50">
              <input
                type="checkbox"
                checked={monitoredRepos.has(repo.full_name)}
                onChange={() => toggleRepo(repo.full_name)}
                className="mt-0.5"
              />
              <div className="flex-1 min-w-0">
                <div className="font-medium text-slate-800">
                  {repo.full_name}
                  {repo.private && <span className="ml-2 px-1.5 py-0.5 bg-slate-100 text-slate-600 text-xs rounded">private</span>}
                </div>
                {repo.description && (
                  <div className="text-sm text-slate-500 truncate">{repo.description}</div>
                )}
              </div>
            </label>
          ))
        )}
      </div>
    </div>
  )
}

interface GitHubRepoFormProps {
  accountId: number
  existingRepos: string[] // repo_path format like "owner/name"
  repoIdMap: Map<string, number> // map repo_path to repo id for deletion
  onAdd: (data: any) => Promise<void>
  onRemove: (repoId: number) => Promise<void>
  onComplete: () => void  // Called after successful changes (triggers reload)
  /** Called when the batch stopped partway, so the form diffs against what is now tracked. */
  onPartialFailure: () => Promise<void>
  onCancel: () => void    // Called when cancelled without changes
}

const plural = (n: number) => `${n} ${n === 1 ? 'repo' : 'repos'}`

const GitHubRepoForm = ({ accountId, existingRepos, repoIdMap, onAdd, onRemove, onComplete, onPartialFailure, onCancel }: GitHubRepoFormProps) => {
  const [monitoredRepos, setMonitoredRepos] = useState<Set<string>>(() => new Set(existingRepos))
  const [formData, setFormData] = useState({
    track_issues: true,
    track_prs: true,
    track_comments: true,
    track_project_fields: false,
    tags: [] as string[],
    check_interval: 60,
    content_mode: 'full' as GithubContentMode,
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string | null>(null)

  // Calculate what changed
  const initialSet = new Set(existingRepos)
  const toAdd = Array.from(monitoredRepos).filter(r => !initialSet.has(r))
  const toRemove = existingRepos.filter(r => !monitoredRepos.has(r))
  const hasChanges = toAdd.length > 0 || toRemove.length > 0

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!hasChanges) {
      onCancel()
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      // Remove unselected repos
      for (const repoPath of toRemove) {
        const repoId = repoIdMap.get(repoPath)
        if (repoId) {
          setProgress(`Removing ${repoPath}...`)
          await onRemove(repoId)
        }
      }

      // Add newly selected repos
      for (const repoPath of toAdd) {
        const [owner, name] = repoPath.split('/')
        setProgress(`Adding ${repoPath}...`)
        await onAdd({ ...formData, owner, name })
      }

      onComplete() // Close modal and trigger single reload
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update repos')
      await onPartialFailure().catch(() => {})
    } finally {
      setSubmitting(false)
      setProgress(null)
    }
  }

  return (
    <Modal title="Manage repos" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field>
          <RepoSelector
            accountId={accountId}
            monitoredRepos={monitoredRepos}
            onChange={setMonitoredRepos}
          />
        </Field>

        {toAdd.length > 0 && (
          <>
            <Fieldset legend="Track for new repos">
              <div className="flex flex-wrap gap-4">
                <Checkbox checked={formData.track_issues} onChange={e => setFormData({ ...formData, track_issues: e.target.checked })} label="Issues" />
                <Checkbox checked={formData.track_prs} onChange={e => setFormData({ ...formData, track_prs: e.target.checked })} label="Pull requests" />
                <Checkbox checked={formData.track_comments} onChange={e => setFormData({ ...formData, track_comments: e.target.checked })} label="Comments" />
                <Checkbox checked={formData.track_project_fields} onChange={e => setFormData({ ...formData, track_project_fields: e.target.checked })} label="Project fields" />
              </div>
              <Field label="Content" hint={CONTENT_MODE_HINT[formData.content_mode]}>
                <Select value={formData.content_mode} onChange={e => setFormData({ ...formData, content_mode: e.target.value as GithubContentMode })}>
                  <option value="full">Full content</option>
                  <option value="metadata">Metadata only</option>
                </Select>
              </Field>
            </Fieldset>

            <SyncScheduleFields value={formData} onChange={setFormData} />

            <Field label="Tags (new repos)">
              <TagsInput
                tags={formData.tags}
                onChange={tags => setFormData({ ...formData, tags })}
              />
            </Field>
          </>
        )}

        {toRemove.length > 0 && <Notice tone="warning">Saving stops tracking {toRemove.join(', ')}.</Notice>}

        <FormActions>
          <Button type="button" onClick={onCancel} disabled={submitting}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={submitting || !hasChanges}>
            {progress
              ? progress
              : !hasChanges
                ? 'No changes'
                : toAdd.length > 0 && toRemove.length > 0
                  ? `Add ${toAdd.length}, remove ${toRemove.length}`
                  : toAdd.length > 0
                    ? `Add ${plural(toAdd.length)}`
                    : `Remove ${plural(toRemove.length)}`}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default GitHubPanel
