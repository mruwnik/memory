/** The grid runs from the start hour to the end hour on each day, so both ranges must be non-empty. */
export const rangeErrors = ({ date_start, date_end, time_start, time_end }: { date_start: string; date_end: string; time_start: number; time_end: number }) => ({
  date: date_start && date_end && date_end < date_start ? 'End date must be on or after the start date' : null,
  hour: time_end <= time_start ? 'End hour must be after the start hour' : null,
})
