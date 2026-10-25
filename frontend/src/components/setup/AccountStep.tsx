import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Button, Card, Field, FormActions, Input, Notice, Page, useSubmit } from '@/components/shared/ui'
import { NewPasswordFields, passwordIsValid } from '@/components/users/NewPasswordFields'
import { storeSession } from '@/hooks/useAuth'
import { createAccount, getNeedsAccount } from '@/hooks/useSetup'

/** Creates this server's first account, as its admin, and signs it in. */
export const AccountStep = ({ onCreated }: { onCreated: () => Promise<void> }) => {
  const [needed, setNeeded] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [token, setToken] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const { busy, error, submit } = useSubmit()

  const load = useCallback(() => {
    setLoadError(null)
    getNeedsAccount().then(setNeeded, e => setLoadError(e.message))
  }, [])
  useEffect(load, [load])

  const create = (e: FormEvent) => {
    e.preventDefault()
    setSubmitted(true)
    if (!passwordIsValid(password, confirm)) return
    submit(async () => {
      storeSession(await createAccount({ name: name.trim(), email: email.trim(), password, token: token.trim() }))
      await onCreated()
    })
  }

  return (
    <Page title={needed === false ? 'Already set up' : 'Create your account'} width="narrow"
      description={needed === false ? undefined : 'This account administers this server. Setup then walks you through LLM keys, a first source and your AI client.'}
      loading={needed === null && !loadError} error={loadError} onRetry={load}>
      {!needed ? (
        <Notice tone="info">
          This server is already set up. <Link to="/ui/login" className="underline">Log in</Link>
        </Notice>
      ) : (
        <Card>
          <form onSubmit={create} className="space-y-4">
            <Field label="Name">
              <Input required autoComplete="name" maxLength={255} value={name} onChange={e => setName(e.target.value)} />
            </Field>
            <Field label="Email">
              <Input type="email" required autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} />
            </Field>
            <NewPasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} submitted={submitted} />
            <Field label="Setup token"
              hint={<>Printed in the api log at startup: run <code>docker compose logs api | grep "setup token"</code> on the server. If it has rotated out of the log, restart the api to print a new one.</>}>
              <Input required autoComplete="off" spellCheck={false} value={token} onChange={e => setToken(e.target.value)} />
            </Field>
            {error && <Notice tone="error">{error}</Notice>}
            <FormActions>
              <Button type="submit" variant="primary" disabled={busy}>{busy ? 'Creating...' : 'Create account'}</Button>
            </FormActions>
          </form>
        </Card>
      )}
    </Page>
  )
}
