// localStorage throws when the browser blocks it (site data disabled, some private modes) or it is full.
// These helpers treat that as "nothing saved" so a remembered preference never breaks a page.

export const readStorage = (key: string): string | null => {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export const writeStorage = (key: string, value: string): void => {
  try {
    localStorage.setItem(key, value)
  } catch {
    // The preference just isn't remembered.
  }
}

/** A saved JSON array, or null when nothing (or something else) is saved. */
export const readStoredArray = <T>(key: string): T[] | null => {
  const saved = readStorage(key)
  if (!saved) return null
  try {
    const parsed: unknown = JSON.parse(saved)
    return Array.isArray(parsed) ? parsed as T[] : null
  } catch {
    return null
  }
}
