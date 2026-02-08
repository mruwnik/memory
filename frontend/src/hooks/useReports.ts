import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { useMCP } from './useMCP'

export interface Report {
  id: number
  title: string | null
  filename: string | null
  tags: string[]
  inserted_at: string | null
  modality: string
  mime_type: string | null
  size: number | null
  preview: string | null
  metadata: {
    report_title?: string | null
    report_format?: string
    [key: string]: any
  } | null
}

export const useReports = () => {
  const { mcpCall } = useMCP()
  const { apiCall } = useAuth()

  const listReports = useCallback(async (): Promise<Report[]> => {
    const result = await mcpCall('core_list_items', {
      modalities: ['doc'],
      limit: 200,
      sort_by: 'inserted_at',
      sort_order: 'desc',
      include_metadata: true,
    })
    const data = result?.[0] || { items: [] }
    // Filter to only reports (have report_format in metadata)
    return (data.items || []).filter(
      (item: Report) => item.metadata?.report_format
    )
  }, [mcpCall])

  const createReport = useCallback(async (
    title: string, content: string, tags?: string[]
  ) => {
    const result = await mcpCall('reports_upsert', { title, content, tags })
    return result?.[0]
  }, [mcpCall])

  const uploadReport = useCallback(async (file: File, title?: string, tags?: string) => {
    const formData = new FormData()
    formData.append('file', file)
    if (title) formData.append('title', title)
    if (tags) formData.append('tags', tags)
    const response = await apiCall('/reports/upload', {
      method: 'POST',
      body: formData,
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.detail || 'Upload failed')
    }
    return response.json()
  }, [apiCall])

  return { listReports, createReport, uploadReport }
}
