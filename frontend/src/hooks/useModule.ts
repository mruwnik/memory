import { createContext, useContext } from 'react'

export type Module = 'claude_containers' | 'multi_user' | 'teams' | 'projects'

/** The modules this instance runs, from `/auth/me`. */
export const ModulesContext = createContext<ReadonlySet<string> | null>(null)

// No default: a render outside the provider would otherwise run with every module off
export const useModules = () => {
  const modules = useContext(ModulesContext)
  if (!modules) throw new Error('useModules needs a ModulesContext provider')
  return modules
}

export const useModule = (name: Module) => useModules().has(name)
