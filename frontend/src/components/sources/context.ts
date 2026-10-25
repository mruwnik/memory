import { createContext, useContext } from 'react'
import { SelectedUser } from '../common/UserSelector'

/** What the Sources page shares with its panels: the selected user, and a way to refresh the tab badges. */
interface SourcesContextType {
  selectedUser: SelectedUser
  userId: number | undefined
  /** Refetches the tab badges; panels call it whenever they reload their list. */
  refreshCounts: () => void
}

export const SourcesContext = createContext<SourcesContextType>({
  selectedUser: { type: 'user', id: 0, name: '' },
  userId: undefined,
  refreshCounts: () => {},
})

export const useSourcesContext = () => useContext(SourcesContext)
