// Kept out of ui.tsx so pages that never render markdown don't load react-markdown.
import ReactMarkdown, { type Components } from 'react-markdown'
import { cx } from '@/components/shared/ui'

// Text inherits its colour, so markdown reads correctly inside a Notice as well as on white.
const COMPONENTS: Components = {
  h1: ({ children }) => <h1 className="text-2xl font-bold mb-4 mt-6 first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="text-xl font-semibold mb-3 mt-5 first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="text-lg font-semibold mb-2 mt-4 first:mt-0">{children}</h3>,
  p: ({ children }) => <p className="mb-4 leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="list-disc list-inside mb-4 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside mb-4 space-y-1">{children}</ol>,
  a: ({ href, children }) => <a href={href} className="text-primary hover:underline">{children}</a>,
  code: ({ className, children }) => className?.includes('language-')
    ? <code className={cx('block bg-slate-100 p-4 rounded-lg text-sm font-mono text-slate-700 overflow-x-auto', className)}>{children}</code>
    : <code className="bg-slate-100 px-1.5 py-0.5 rounded text-sm font-mono text-slate-700">{children}</code>,
  pre: ({ children }) => <pre className="mb-4">{children}</pre>,
  blockquote: ({ children }) => <blockquote className="border-l-4 border-slate-300 pl-4 italic mb-4">{children}</blockquote>,
  hr: () => <hr className="border-slate-200 my-6" />,
}

export const Markdown = ({ children, className }: { children: string; className?: string }) => (
  <div className={cx('whitespace-normal [&>*:last-child]:mb-0', className)}>
    <ReactMarkdown components={COMPONENTS}>{children}</ReactMarkdown>
  </div>
)
