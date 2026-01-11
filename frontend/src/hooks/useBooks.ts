import { useCallback } from 'react'
import { useMCP } from './useMCP'
import { useAuth } from './useAuth'

export interface Book {
  id: number
  title: string
  author: string | null
  publisher: string | null
  published: string | null
  language: string | null
  total_pages: number | null
  tags: string[]
  section_count: number
  file_path: string | null
}

export interface BookSection {
  id: number
  title: string
  content: string
  order: number
  parent_section_id: number | null
}

export interface BookFilters {
  title?: string
  author?: string
  tags?: string[]
  sections?: boolean
  limit?: number
  offset?: number
}

export const useBooks = () => {
  const { mcpCall } = useMCP()
  const { apiCall } = useAuth()

  const listBooks = useCallback(async (filters: BookFilters = {}): Promise<Book[]> => {
    const result = await mcpCall<Book[][]>('books_list_books', {
      title: filters.title,
      author: filters.author,
      tags: filters.tags,
      sections: filters.sections ?? false,
      limit: filters.limit ?? 50,
      offset: filters.offset ?? 0,
    })
    // mcpCall returns array from .map(), unwrap the first element
    return result?.[0] || []
  }, [mcpCall])

  const readBook = useCallback(async (bookId: number, sectionIds?: number[]): Promise<BookSection[]> => {
    const result = await mcpCall<BookSection[][]>('books_read_book', {
      book_id: bookId,
      sections: sectionIds ?? [],
    })
    // mcpCall returns array from .map(), unwrap the first element
    return result?.[0] || []
  }, [mcpCall])

  // Upload remains as API call since file uploads aren't MCP operations
  const uploadBook = useCallback(async (file: File): Promise<{ task_id: string; status: string }> => {
    const formData = new FormData()
    formData.append('file', file)

    const response = await apiCall('/books/upload', {
      method: 'POST',
      body: formData,
    })

    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.detail || 'Upload failed')
    }

    return response.json()
  }, [apiCall])

  return {
    listBooks,
    readBook,
    uploadBook,
  }
}
