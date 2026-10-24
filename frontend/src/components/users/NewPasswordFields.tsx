import { Field, Input } from '@/components/shared/ui'

export const MIN_PASSWORD_LENGTH = 8

export const passwordProblems = (password: string, confirm: string) => ({
  password: password.length < MIN_PASSWORD_LENGTH ? `Password must be at least ${MIN_PASSWORD_LENGTH} characters` : null,
  confirm: password !== confirm ? 'Passwords do not match' : null,
})

export const passwordIsValid = (password: string, confirm: string) => {
  const problems = passwordProblems(password, confirm)
  return !problems.password && !problems.confirm
}

/** New password and its confirmation; the problems show on the fields once the form was submitted. */
export const NewPasswordFields = ({ password, confirm, onPassword, onConfirm, submitted }: {
  password: string
  confirm: string
  onPassword: (value: string) => void
  onConfirm: (value: string) => void
  submitted: boolean
}) => {
  const problems = passwordProblems(password, confirm)
  return (
    <>
      <Field label="New password" hint={`At least ${MIN_PASSWORD_LENGTH} characters`} error={submitted ? problems.password : null}>
        <Input type="password" autoComplete="new-password" value={password} onChange={(e) => onPassword(e.target.value)} />
      </Field>
      <Field label="Confirm new password" error={submitted ? problems.confirm : null}>
        <Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => onConfirm(e.target.value)} />
      </Field>
    </>
  )
}
