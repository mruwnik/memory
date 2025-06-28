import { useEffect, useCallback } from 'react'
import { useAuth } from '@/hooks/useAuth'

const parseServerSentEvents = async (response: Response): Promise<any> => {
  const reader = response.body?.getReader()
  const decoder = new TextDecoder()
  let buffer = '' // Buffer for incomplete lines
  const events: any[] = []

  if (reader) {
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          break
        }

        // Decode the chunk and add to buffer
        const chunk = decoder.decode(value, { stream: true })
        buffer += chunk

        // Process complete lines
        const lines = buffer.split('\n')

        // Keep the last line in buffer if it doesn't end with \n
        if (!buffer.endsWith('\n')) {
          buffer = lines.pop() || ''
        } else {
          buffer = ''
        }

        // Process each complete line to build SSE events
        let currentEvent: { event?: string; data?: string; id?: string } = {}

        for (const line of lines) {
          if (line.trim() === '') {
            // Empty line marks end of event
            if (currentEvent.data) {
              try {
                const parsed = JSON.parse(currentEvent.data)
                events.push(parsed)
              } catch (e) {
                console.warn('Failed to parse SSE event data:', currentEvent.data)
              }
            }
            currentEvent = {}
          } else if (line.startsWith('event: ')) {
            currentEvent.event = line.slice(7)
          } else if (line.startsWith('data: ')) {
            currentEvent.data = line.slice(6)
          } else if (line.startsWith('id: ')) {
            currentEvent.id = line.slice(4)
          }
          // Ignore other SSE fields like retry:
        }

        // Handle case where last event doesn't end with empty line
        if (buffer === '' && currentEvent.data) {
          try {
            const parsed = JSON.parse(currentEvent.data)
            events.push(parsed)
          } catch (e) {
            console.warn('Failed to parse final SSE event data:', currentEvent.data)
          }
        }
      }
    } catch (error) {
      console.error('Error reading SSE stream:', error)
      throw error
    } finally {
      reader.releaseLock()
    }
  }

  // For MCP, we expect one JSON-RPC response, so return the last/only event
  if (events.length === 0) {
    throw new Error('No valid SSE events received')
  }

  // Return the last event (which should be the JSON-RPC response)
  return events[events.length - 1]
}

const parseJsonRpcResponse = async (response: Response): Promise<any> => {
  const contentType = response.headers.get('content-type')
  if (contentType?.includes('text/event-stream')) {
    return parseServerSentEvents(response)
  } else {
    return response.json()
  }
}

export const useMCP = () => {
  const { apiCall, checkAuth } = useAuth()

  const mcpCall = useCallback(async (method: string, params: any = {}) => {
    const response = await apiCall(`/mcp/${method}`, {
      method: 'POST',
      headers: {
        'Accept': 'application/json, text/event-stream',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: Date.now(),
        method: "tools/call",
        params: {
          name: method,
          arguments: params,
        },
      }),
    })

    if (!response.ok) {
      throw new Error(`MCP call failed: ${response.statusText}`)
    }

    const resp = await parseJsonRpcResponse(response)
    if (resp?.result?.isError) {
      throw new Error(resp?.result?.content[0].text)
    }
    return resp?.result?.content.map((item: any) => {
      try {
        return JSON.parse(item.text)
      } catch (e) {
        return item.text
      }
    })
  }, [apiCall])

  const listNotes = useCallback(async (path: string = "/") => {
    return await mcpCall('note_files', { path })
  }, [mcpCall])

  const fetchFile = useCallback(async (filename: string) => {
    return await mcpCall('fetch_file', { filename })
  }, [mcpCall])

  const getTags = useCallback(async () => {
    return await mcpCall('get_all_tags')
  }, [mcpCall])

  const getSubjects = useCallback(async () => {
    return await mcpCall('get_all_subjects')
  }, [mcpCall])

  const getObservationTypes = useCallback(async () => {
    return await mcpCall('get_all_observation_types')
  }, [mcpCall])

  const getMetadataSchemas = useCallback(async () => {
    return (await mcpCall('get_metadata_schemas'))[0]
  }, [mcpCall])

  const searchKnowledgeBase = useCallback(async (query: string, modalities: string[] = [], filters: Record<string, any> = {}, config: Record<string, any> = {}) => {
    return await mcpCall('search_knowledge_base', {
      query,
      filters,
      config,
      modalities,
    })
  }, [mcpCall])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  return {
    mcpCall,
    fetchFile,
    listNotes,
    searchKnowledgeBase,
    getTags,
    getSubjects,
    getObservationTypes,
    getMetadataSchemas,
  }
} 