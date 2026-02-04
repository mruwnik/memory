import { useCallback } from 'react'
import { useMCP } from './useMCP'
import { JournalEntry } from './useProjects'

export const useJournal = () => {
  const { mcpCall } = useMCP()

  const addJournalEntry = useCallback(
    async (
      targetId: number,
      content: string,
      targetType: 'source_item' | 'project' | 'team' | 'poll' = 'source_item',
      isPrivate: boolean = false
    ): Promise<{ status: string; entry: JournalEntry }> => {
      const result = await mcpCall('journal_add', {
        target_id: targetId,
        content,
        target_type: targetType,
        private: isPrivate,
      })
      return result[0] as { status: string; entry: JournalEntry }
    },
    [mcpCall]
  )

  return {
    addJournalEntry,
  }
}
