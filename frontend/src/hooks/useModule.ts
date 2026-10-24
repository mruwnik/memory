import { createContext, useContext } from 'react'

export type Module = 'claude_containers' | 'multi_user' | 'teams' | 'projects'

/** The modules this instance runs, from `/auth/me`. */
export const ModulesContext = createContext<ReadonlySet<string>>(new Set())

export const useModules = () => useContext(ModulesContext)

export const useModule = (name: Module) => useModules().has(name)
