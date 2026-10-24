import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Button, ButtonLink, Card, Field, FormActions, Input, Notice, Page } from '@/components/shared/ui'
import { confirmReset } from '@/hooks/passwordReset'

const MIN_LENGTH = 8

const ResetPassword = () => {
  // The token rides in the fragment so it never reaches server logs; read it once, then drop it from history.
  const [token] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get('token'))
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [mismatch, setMismatch] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    window.history.replaceState(window.history.state, '', window.location.pathname)
  }, [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setMismatch(password !== confirm)
    if (password !== confirm || !token) return
    setSubmitting(true)
    setError(null)
    try {
      await confirmReset(token, password)
      setDone(true)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const newLink = <Link to="/ui/forgot-password" className="underline">Request a new link</Link>

  return (
    <Page title="Choose a new password" width="narrow">
      {!token ? (
        <Notice tone="error"><p>This link is incomplete. Copy the whole link from the email.</p>{newLink}</Notice>
      ) : done ? (
        <Card>
          <Notice tone="success">Password changed and all your sessions ended; API keys are unchanged (manage them in Settings). Log in with your new password.</Notice>
          <FormActions>
            <ButtonLink to="/ui/login" variant="primary">Log in</ButtonLink>
          </FormActions>
        </Card>
      ) : (
        <Card>
          <form onSubmit={submit} className="space-y-4">
            <Field label="New password" hint={`At least ${MIN_LENGTH} characters.`}>
              <Input type="password" required minLength={MIN_LENGTH} autoComplete="new-password"
                value={password} onChange={e => setPassword(e.target.value)} />
            </Field>
            <Field label="Confirm new password" error={mismatch ? "The passwords don't match." : null}>
              <Input type="password" required minLength={MIN_LENGTH} autoComplete="new-password"
                value={confirm} onChange={e => setConfirm(e.target.value)} />
            </Field>
            {error && <Notice tone="error"><p>{error}</p>{newLink}</Notice>}
            <FormActions>
              <Button type="submit" variant="primary" disabled={submitting}>Set password</Button>
            </FormActions>
          </form>
        </Card>
      )}
    </Page>
  )
}

export default ResetPassword
