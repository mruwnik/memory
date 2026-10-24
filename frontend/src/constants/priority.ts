// Priority and due-date presentation shared by task views
import type { Tone } from '@/components/shared/ui'

export const PRIORITY_ORDER: Record<string, number> = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
}

export const PRIORITY_TONES: Record<string, Tone> = {
  urgent: 'danger',
  high: 'warning',
  medium: 'info',
  low: 'neutral',
}

/** "Overdue", "Today", "Tomorrow" or a short date, with the tone to show it in. */
export const dueLabel = (dueDate: string, now = new Date()): { text: string; tone: Tone } => {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const date = new Date(dueDate)
  const days = Math.round((day(date) - day(now)) / 86400000)
  if (days < 0) return { text: 'Overdue', tone: 'danger' }
  if (days === 0) return { text: 'Today', tone: 'warning' }
  if (days === 1) return { text: 'Tomorrow', tone: 'info' }
  return { text: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }), tone: 'neutral' }
}
