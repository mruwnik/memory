import { useState } from 'react'
import { Button, FormActions, Notice, copyText } from '@/components/shared/ui'

/** A just-generated API key, shown once with a way to copy it. */
export const NewApiKey = ({ apiKey, onDone }: { apiKey: string; onDone: () => void }) => {
  const [copied, setCopied] = useState<boolean | null>(null)
  const copy = () => copyText(apiKey).then(() => setCopied(true), () => setCopied(false))
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">Copy the API key now and keep it somewhere safe. It won't be shown again.</p>
      <div className="bg-slate-100 p-3 rounded-lg">
        <code className="text-sm text-slate-800 break-all">{apiKey}</code>
      </div>
      {copied === false && <Notice tone="error">Couldn't copy automatically. Select the key and copy it by hand.</Notice>}
      <FormActions>
        <Button onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
        <Button variant="primary" onClick={onDone}>Done</Button>
      </FormActions>
    </div>
  )
}
