import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Button, ButtonLink, Card, Field, FormActions, Input, Notice, Page } from '@/components/shared/ui'
import { getResetAvailable, requestReset } from '@/hooks/passwordReset'

const ForgotPassword = () => {
  const [available, setAvailable] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoadError(null)
    getResetAvailable().then(setAvailable, e => setLoadError(e.message))
  }, [])
  useEffect(load, [load])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await requestReset(email)
      setSent(true)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Page title="Reset your password" back={{ to: '/ui/login', label: 'Log in' }} width="narrow"
      loading={available === null && !loadError} error={loadError} onRetry={load}>
      {!available ? (
        <Notice tone="info">Password reset by email isn't set up on this server. Ask an admin to reset your password.</Notice>
      ) : sent ? (
        <Notice tone="success">
          If an account exists for {email}, we've sent it a reset link. The link expires in 30 minutes.
        </Notice>
      ) : (
        <Card>
          <form onSubmit={submit} className="space-y-4">
            <Field label="Email" hint="We'll email you a link to choose a new password.">
              <Input type="email" required autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} />
            </Field>
            {error && <Notice tone="error">{error}</Notice>}
            <FormActions>
              <ButtonLink to="/ui/login">Cancel</ButtonLink>
              <Button type="submit" variant="primary" disabled={submitting}>Send reset link</Button>
            </FormActions>
          </form>
        </Card>
      )}
    </Page>
  )
}

export default ForgotPassword
