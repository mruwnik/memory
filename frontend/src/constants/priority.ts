// Priority-related constants used across task components

export const PRIORITY_ORDER: Record<string, number> = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
}

// Tailwind v4 uses CSS variables from @theme, requiring var() syntax
export const PRIORITY_COLORS: Record<string, string> = {
  urgent: 'bg-[var(--color-urgent)]',
  high: 'bg-[var(--color-high)]',
  medium: 'bg-[var(--color-medium)]',
  low: 'bg-[var(--color-low)]',
}

export const PRIORITY_TEXT_COLORS: Record<string, string> = {
  urgent: 'text-[var(--color-urgent)]',
  high: 'text-[var(--color-high)]',
  medium: 'text-[var(--color-medium)]',
  low: 'text-[var(--color-low)]',
}
