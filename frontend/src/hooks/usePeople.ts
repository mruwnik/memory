import { useCallback } from 'react'
import { useMCP } from './useMCP'

export interface Person {
  identifier: string
  display_name: string
  aliases: string[]
  contact_info: Record<string, any>
  tags: string[]
  notes: string | null
  created_at: string | null
}

export interface CreatePersonRequest {
  identifier: string
  display_name: string
  aliases?: string[]
  contact_info?: Record<string, any>
  tags?: string[]
  notes?: string
}

export interface UpdatePersonRequest {
  identifier: string
  display_name?: string
  aliases?: string[]
  contact_info?: Record<string, any>
  tags?: string[]
  notes?: string
  replace_notes?: boolean
}

export interface TaskResult {
  task_id: string
  status: string
  identifier: string
}

export interface DeleteResult {
  deleted: boolean
  identifier: string
  display_name: string
}

export const usePeople = () => {
  const { mcpCall: rawMcpCall } = useMCP()

  // Wrapper that returns first result item (MCP returns array)
  const mcpCall = useCallback(async <T>(method: string, params: Record<string, any> = {}): Promise<T> => {
    const result = await rawMcpCall(method, params)
    return Array.isArray(result) ? result[0] : result
  }, [rawMcpCall])

  const listPeople = useCallback(async (
    tags?: string[],
    search?: string,
    limit: number = 50,
    offset: number = 0
  ): Promise<Person[]> => {
    // listPeople returns an array directly, so use rawMcpCall and get first element
    const result = await rawMcpCall('people_list_people', {
      tags,
      search,
      limit,
      offset,
    })
    // MCP returns array of results, first element is our Person[]
    return (Array.isArray(result) ? result[0] : result) || []
  }, [rawMcpCall])

  const getPerson = useCallback(async (identifier: string): Promise<Person | null> => {
    return mcpCall<Person | null>('people_get_person', { identifier })
  }, [mcpCall])

  const createPerson = useCallback(async (data: CreatePersonRequest): Promise<TaskResult> => {
    return mcpCall<TaskResult>('people_add', {
      identifier: data.identifier,
      display_name: data.display_name,
      aliases: data.aliases,
      contact_info: data.contact_info,
      tags: data.tags,
      notes: data.notes,
    })
  }, [mcpCall])

  const updatePerson = useCallback(async (data: UpdatePersonRequest): Promise<TaskResult> => {
    return mcpCall<TaskResult>('people_update', {
      identifier: data.identifier,
      display_name: data.display_name,
      aliases: data.aliases,
      contact_info: data.contact_info,
      tags: data.tags,
      notes: data.notes,
      replace_notes: data.replace_notes,
    })
  }, [mcpCall])

  const deletePerson = useCallback(async (identifier: string): Promise<DeleteResult> => {
    return mcpCall<DeleteResult>('people_delete', { identifier })
  }, [mcpCall])

  return {
    listPeople,
    getPerson,
    createPerson,
    updatePerson,
    deletePerson,
  }
}
