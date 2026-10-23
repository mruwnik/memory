import { LoadingState } from '@/components/shared/ui'

/** Full-screen loading, for before the app shell exists (the auth check). */
const Loading = ({ message }: { message?: string }) => (
  <div className="min-h-screen grid place-items-center">
    <LoadingState message={message} />
  </div>
)

export default Loading
