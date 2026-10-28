import { useMemo } from 'react'
import { Combobox } from '@/components/shared/ui'
import { timezoneOptions } from '@/utils/timezones'

/** Common zones plus any IANA name typed in. */
export const TimezoneCombobox = (props: { value: string; onChange: (value: string) => void; id?: string; 'aria-describedby'?: string }) => {
  const options = useMemo(timezoneOptions, [])
  return <Combobox {...props} options={options} allowCustom />
}
