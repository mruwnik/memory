import { useCallback, useRef } from 'react'

/**
 * Lets only the newest of overlapping requests write state. Call the returned `begin` when a request
 * starts; it gives back `isStale()`, which turns true as soon as a newer request begins.
 */
export const useNewestRequest = () => {
  const latest = useRef(0)
  return useCallback(() => {
    const request = ++latest.current
    return () => request !== latest.current
  }, [])
}
