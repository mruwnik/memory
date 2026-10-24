import { useCallback } from 'react'
import { useMCP } from './useMCP'

export interface Tidbit {
  id: number
  person_id: number
  content: string
  tidbit_type: string | null
  source: string | null
  sensitivity: string | null
  project_id: number | null
  tags: string[]
  creator_id: number | null
  created_at: string | null
}

export interface Person {
  id: number
  identifier: string
  display_name: string
  aliases: string[]
  contact_info: Record<string, string>
  tags: string[]
  created_at: string | null
  tidbits?: Tidbit[]
}

export interface PersonCreate {
  identifier: string
  display_name: string
  aliases?: string[]
  contact_info?: Record<string, string>
  content?: string  // Initial tidbit content
  tidbit_type?: string
  tags?: string[]
  project_id?: number
  sensitivity?: string
}

export interface PersonUpdate {
  display_name?: string
  /** A list adds aliases; in the map form true adds an alias and null removes it. */
  aliases?: string[] | Record<string, true | null>
  /** Merged into the stored contact info; null removes a key. */
  contact_info?: Record<string, string | null>
  content?: string  // Add a tidbit
  tidbit_type?: string
  tags?: string[]
  project_id?: number
  sensitivity?: string
}

export interface TidbitInput {
  content: string
  tidbit_type: string
  tags: string[]
  sensitivity: string
}

export interface PersonFilters {
  tags?: string[]
  search?: string
  limit?: number
  offset?: number
}

export const usePeople = () => {
  const { mcpCall } = useMCP()

  // Note: mcpCall returns results wrapped in an array from the SSE response parsing.
  // The result?.[0] pattern unwraps the first (and only) response element.

  const listPeople = useCallback(async (filters: PersonFilters = {}): Promise<Person[]> => {
    const result = await mcpCall<{ people: Person[]; error?: string }[]>('people_list_all', {
      tags: filters.tags,
      search: filters.search,
      limit: filters.limit ?? 50,
      offset: filters.offset ?? 0,
    })
    // list_all returns a list directly, not wrapped in { people: ... }
    return (result?.[0] as unknown as Person[]) || []
  }, [mcpCall])

  const getPerson = useCallback(async (identifier: string, includeTidbits = true): Promise<Person | null> => {
    // people_fetch returns the person dict directly, not wrapped in { person: ... }
    const result = await mcpCall<(Person | null)[]>('people_fetch', {
      identifier,
      include_tidbits: includeTidbits,
    })
    return result?.[0] || null
  }, [mcpCall])

  const addPerson = useCallback(async (data: PersonCreate): Promise<{ success: boolean; person?: Person; error?: string }> => {
    const result = await mcpCall<{ success: boolean; person?: Person; error?: string }[]>('people_upsert', {
      identifier: data.identifier,
      display_name: data.display_name,
      aliases: data.aliases,
      contact_info: data.contact_info,
      content: data.content,
      tidbit_type: data.tidbit_type,
      tags: data.tags,
      project_id: data.project_id,
      sensitivity: data.sensitivity,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const updatePerson = useCallback(async (identifier: string, data: PersonUpdate): Promise<{ success: boolean; person?: Person; error?: string }> => {
    const result = await mcpCall<{ success: boolean; person?: Person; error?: string }[]>('people_upsert', {
      identifier,
      display_name: data.display_name,
      aliases: data.aliases,
      contact_info: data.contact_info,
      content: data.content,
      tidbit_type: data.tidbit_type,
      tags: data.tags,
      project_id: data.project_id,
      sensitivity: data.sensitivity,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const deletePerson = useCallback(async (identifier: string): Promise<{ deleted: boolean; identifier: string; display_name: string }> => {
    const result = await mcpCall<{ deleted: boolean; identifier: string; display_name: string }[]>('people_delete', {
      identifier,
    })
    return result?.[0]
  }, [mcpCall])

  const mergePeople = useCallback(async (
    identifiers: string[],
    primaryIdentifier?: string
  ): Promise<{
    success: boolean
    primary?: { identifier: string; display_name: string; aliases: string[] }
    merged_from?: string[]
    stats?: Record<string, number>
    error?: string
  }> => {
    const result = await mcpCall<{
      success: boolean
      primary?: { identifier: string; display_name: string; aliases: string[] }
      merged_from?: string[]
      stats?: Record<string, number>
      error?: string
    }[]>('people_merge', {
      identifiers,
      primary_identifier: primaryIdentifier,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const addTidbit = useCallback(async (identifier: string, data: TidbitInput) => {
    await mcpCall('people_tidbit_add', { identifier, ...data })
  }, [mcpCall])

  const updateTidbit = useCallback(async (tidbitId: number, data: TidbitInput) => {
    await mcpCall('people_tidbit_update', { tidbit_id: tidbitId, ...data })
  }, [mcpCall])

  const deleteTidbit = useCallback(async (tidbitId: number) => {
    await mcpCall('people_tidbit_delete', { tidbit_id: tidbitId })
  }, [mcpCall])

  return {
    addTidbit,
    updateTidbit,
    deleteTidbit,
    listPeople,
    getPerson,
    addPerson,
    updatePerson,
    deletePerson,
    mergePeople,
  }
}
