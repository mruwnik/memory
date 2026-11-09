/** Dollars to the cent. A non-zero amount under a cent shows as "<$0.01" rather than "$0.00" or six decimals. */
export const formatCost = (amount: number | string): string => {
  const usd = Number(amount)
  return usd > 0 && usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`
}

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

/** A byte count in the largest unit (up to TB) that keeps it at least 1, to one decimal place above bytes. */
export const formatBytes = (bytes: number): string => {
  const unit = Math.min(BYTE_UNITS.length - 1, Math.floor(Math.log(Math.max(bytes, 1)) / Math.log(1024)))
  return unit === 0 ? `${bytes} B` : `${(bytes / 1024 ** unit).toFixed(1)} ${BYTE_UNITS[unit]}`
}
