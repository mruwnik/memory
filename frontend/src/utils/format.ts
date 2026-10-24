/** Dollars to the cent. A non-zero amount under a cent shows as "<$0.01" rather than "$0.00" or six decimals. */
export const formatCost = (amount: number | string): string => {
  const usd = Number(amount)
  return usd > 0 && usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`
}
