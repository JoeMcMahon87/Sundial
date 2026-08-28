import type { CalendarEvent } from '../api'
import { EventChip } from './EventChip'

/** All-day events sit above the timeline; they have no position within it. */
export function AllDayStrip({
  events,
  timeZone,
}: {
  events: CalendarEvent[]
  timeZone: string
}) {
  if (events.length === 0) return null
  return (
    <div
      className="flex flex-col gap-1 border-b border-paper/10 pb-2"
      aria-label="All-day events"
    >
      {events.map((event) => (
        <div key={event.event_id} className="h-7">
          <EventChip event={event} timeZone={timeZone} compact />
        </div>
      ))}
    </div>
  )
}
