import { useCallback } from 'react'
import { useMCP } from './useMCP'

export interface Person {
  identifier: string
  display_name: string
  aliases: string[]
  contact_info: Record<string, string>
  tags: string[]
  notes: string | null
  created_at: string | null
}

export interface PersonCreate {
  identifier: string
  display_name: string
  aliases?: string[]
  contact_info?: Record<string, string>
  tags?: string[]
  notes?: string
}

export interface PersonUpdate {
  display_name?: string
  aliases?: string[]
  contact_info?: Record<string, string>
  tags?: string[]
  notes?: string
  replace_notes?: boolean
}

export interface PersonFilters {
  tags?: string[]
  search?: string
  limit?: number
  offset?: number
}

export interface TaskResult {
  task_id: string
  status: string
  identifier: string
}

export const usePeople = () => {
  const { mcpCall } = useMCP()

  // Note: mcpCall returns results wrapped in an array from the SSE response parsing.
  // The result?.[0] pattern unwraps the first (and only) response element.
  // The double-array typing (e.g., Person[][]) reflects: outer array from mcpCall,
  // inner array from the actual API response.

  const listPeople = useCallback(async (filters: PersonFilters = {}): Promise<Person[]> => {
    const result = await mcpCall<Person[][]>('people_list_people', {
      tags: filters.tags,
      search: filters.search,
      limit: filters.limit ?? 50,
      offset: filters.offset ?? 0,
    })
    return result?.[0] || []
  }, [mcpCall])

  const getPerson = useCallback(async (identifier: string): Promise<Person | null> => {
    const result = await mcpCall<(Person | null)[]>('people_get_person', {
      identifier,
    })
    return result?.[0] || null
  }, [mcpCall])

  const addPerson = useCallback(async (data: PersonCreate): Promise<TaskResult> => {
    const result = await mcpCall<TaskResult[]>('people_add', {
      identifier: data.identifier,
      display_name: data.display_name,
      aliases: data.aliases,
      contact_info: data.contact_info,
      tags: data.tags,
      notes: data.notes,
    })
    return result?.[0]
  }, [mcpCall])

  const updatePerson = useCallback(async (identifier: string, data: PersonUpdate): Promise<TaskResult> => {
    const result = await mcpCall<TaskResult[]>('people_update', {
      identifier,
      display_name: data.display_name,
      aliases: data.aliases,
      contact_info: data.contact_info,
      tags: data.tags,
      notes: data.notes,
      replace_notes: data.replace_notes,
    })
    return result?.[0]
  }, [mcpCall])

  const deletePerson = useCallback(async (identifier: string): Promise<{ deleted: boolean; identifier: string; display_name: string }> => {
    const result = await mcpCall<{ deleted: boolean; identifier: string; display_name: string }[]>('people_delete', {
      identifier,
    })
    return result?.[0]
  }, [mcpCall])

  return {
    listPeople,
    getPerson,
    addPerson,
    updatePerson,
    deletePerson,
  }
}
