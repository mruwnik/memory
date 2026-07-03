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
  }
  const text = await response.text()
  if (!text) {
    throw new Error('MCP server returned an empty response')
  }
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`MCP server returned non-JSON response: ${text.slice(0, 200)}`)
  }
}

export const useMCP = () => {
  const { apiCall, checkAuth } = useAuth()

  const mcpCall = useCallback(async (method: string, params: any = {}) => {
    // Always POST to exactly /mcp — the tool is named in the JSON-RPC body.
    // fastmcp registers /mcp as an exact route, so subpaths 404.
    const response = await apiCall('/mcp', {
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
      let detail = response.statusText
      try {
        const body = await response.text()
        if (body) detail = `${detail}: ${body.slice(0, 200)}`
      } catch {
        // body already consumed or unreadable — fall back to statusText
      }
      throw new Error(`MCP call ${method} failed (${response.status}) ${detail}`)
    }

    const resp = await parseJsonRpcResponse(response)

    // JSON-RPC error envelope: {jsonrpc, id, error: {code, message, data}}
    if (resp?.error) {
      const {code, message, data} = resp.error
      throw new Error(`MCP ${method} error${code != null ? ` ${code}` : ''}: ${message ?? data ?? 'unknown error'}`)
    }

    const content = resp?.result?.content
    if (!Array.isArray(content)) {
      throw new Error(`MCP ${method} returned malformed response (no result.content)`)
    }

    if (resp.result.isError) {
      const text = content[0]?.text ?? 'unknown error'
      throw new Error(`MCP ${method} tool error: ${text}`)
    }

    return content.map((item: any) => {
      try {
        return JSON.parse(item.text)
      } catch (e) {
        return item.text
      }
    })
  }, [apiCall])

  const listNotes = useCallback(async (path: string = "/") => {
    return await mcpCall('notes_note_files', { path })
  }, [mcpCall])

  const fetchFile = useCallback(async (filename: string) => {
    return await mcpCall('core_fetch_file', { filename })
  }, [mcpCall])

  const saveNote = useCallback(async (filename: string, content: string, subject?: string) => {
    return await mcpCall('notes_upsert', {
      filename,
      content,
      subject: subject || filename.split('/').pop()?.replace('.md', '') || 'Note',
    })
  }, [mcpCall])

  // These meta endpoints no longer exist - return empty results
  // TODO: Find alternative source for tags/subjects/observation types if needed
  const getTags = useCallback(async () => {
    return [[]]  // Return empty array wrapped to match expected format
  }, [])

  const getSubjects = useCallback(async () => {
    return [[]]
  }, [])

  const getObservationTypes = useCallback(async () => {
    return [[]]
  }, [])

  const getMetadataSchemas = useCallback(async () => {
    return (await mcpCall('meta_get_metadata_schemas'))[0]
  }, [mcpCall])

  const searchKnowledgeBase = useCallback(async (query: string, modalities: string[] = [], filters: Record<string, any> = {}, config: Record<string, any> = {}) => {
    return await mcpCall('core_search', {
      query,
      filters,
      modalities,
      limit: config.limit ?? 20,
      previews: config.previews ?? false,
      use_scores: config.useScores ?? false,
    })
  }, [mcpCall])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  return {
    mcpCall,
    fetchFile,
    listNotes,
    saveNote,
    searchKnowledgeBase,
    getTags,
    getSubjects,
    getObservationTypes,
    getMetadataSchemas,
  }
} 