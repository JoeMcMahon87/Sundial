import { useQuery } from '@tanstack/react-query'
import { api, type CalendarEvent } from '../api'
import { addDays, startOfDay } from '../lib/time'

/**
 * Events covering `[firstDay, lastDay]` inclusive, in local terms.
 *
 * The window is expressed as instants because that is what the API takes, but
 * it is derived from local day boundaries — asking for "midnight to midnight
 * UTC" would return the wrong days for everyone outside UTC.
 */
export function useEvents(firstDay: string, lastDay: string, timeZone: string) {
  return useQuery({
    queryKey: ['events', firstDay, lastDay, timeZone],
    queryFn: async (): Promise<CalendarEvent[]> => {
      const from = startOfDay(firstDay, timeZone)
      const to = startOfDay(addDays(lastDay, 1), timeZone)
      const { events } = await api.events(from, to, timeZone)
      return events
    },
    // A webhook-driven sync (M1 step 4) will push staleness down; until then
    // the safety net is the user looking at the screen.
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  })
}
