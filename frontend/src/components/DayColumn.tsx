import type { CalendarEvent } from '../api'
import { layOutDay, nowOffset } from '../lib/layout'
import { minutesToLength } from '../lib/scale'
import { EventChip } from './EventChip'

/**
 * One day's timed events, positioned absolutely.
 *
 * The column's own height comes from the caller so that Today and Week share
 * exactly one layout path — a 23-hour day is short and a 25-hour day is tall,
 * and both are correct.
 */
export function DayColumn({
  events,
  dateKey,
  timeZone,
  compact = false,
}: {
  events: CalendarEvent[]
  dateKey: string
  timeZone: string
  compact?: boolean
}) {
  const { timed } = layOutDay(events, dateKey, timeZone)
  const now = nowOffset(dateKey, timeZone)

  return (
    <div className="relative h-full">
      {timed.map((placed) => (
        <div
          key={placed.event.event_id}
          className="absolute px-px"
          style={{
            top: minutesToLength(placed.top),
            height: minutesToLength(placed.height),
            left: `${(placed.lane / placed.lanes) * 100}%`,
            width: `${(1 / placed.lanes) * 100}%`,
          }}
        >
          <EventChip event={placed.event} timeZone={timeZone} compact={compact} />
        </div>
      ))}

      {now !== null && (
        <div
          className="pointer-events-none absolute inset-x-0 z-10 flex items-center"
          style={{ top: minutesToLength(now) }}
          aria-label="Now"
        >
          <span className="size-2 shrink-0 rounded-full bg-danger" />
          <span className="h-px grow bg-danger" />
        </div>
      )}
    </div>
  )
}
