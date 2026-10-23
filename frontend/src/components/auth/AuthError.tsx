import { ErrorState } from '@/components/shared/ui'

const AuthError = ({ error, onRetry }: { error: string, onRetry: () => void }) => (
  <div className="min-h-screen grid place-items-center p-8 text-center">
    <div>
      <h1 className="text-xl font-semibold text-slate-800">Authentication Error</h1>
      <ErrorState message={error} onRetry={onRetry} />
    </div>
  </div>
)

export default AuthError
