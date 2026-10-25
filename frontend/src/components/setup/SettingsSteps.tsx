import type { ReactNode } from 'react'
import { ModuleToggles, RestartBanner, VOYAGE_SETTING_KEY, VoyageKey, moduleRows, useSettingsState } from '@/components/settings/SettingsPage'
import { Card, LoadingState, Notice } from '@/components/shared/ui'

type SettingsState = ReturnType<typeof useSettingsState>

const Frame = ({ settings, children }: { settings: SettingsState; children: ReactNode }) => (
  <>
    {settings.loadError && <Notice tone="error" onRetry={settings.load}>{settings.loadError}</Notice>}
    {!settings.state && !settings.loadError && <LoadingState message="Loading settings..." />}
    {settings.notice}
    {settings.state && children}
  </>
)

export const VoyageStep = () => {
  const settings = useSettingsState()
  const voyage = settings.state?.settings.find(s => s.key === VOYAGE_SETTING_KEY)
  return (
    <Frame settings={settings}>
      {voyage && <Card><VoyageKey row={voyage} busy={settings.busy} save={settings.save} /></Card>}
    </Frame>
  )
}

export const ModulesStep = ({ onRestarted }: { onRestarted: () => Promise<unknown> }) => {
  const settings = useSettingsState(onRestarted)
  const { state } = settings
  return (
    <Frame settings={settings}>
      {state?.restart_pending && <RestartBanner state={state} onRestart={settings.restart} busy={settings.restarting} />}
      <Card flush><ModuleToggles rows={moduleRows(state)} busy={settings.busy} save={settings.save} /></Card>
    </Frame>
  )
}
