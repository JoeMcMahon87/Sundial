import type { CalendarEvent } from '../api'
import { formatTime } from '../lib/time'

const TONE: Record<CalendarEvent['kind'], string> = {
  // Blocks are Sundial's own; they read as deliberate rather than imposed.
  block: 'bg-accent/25 border-accent text-paper',
  appointment: 'bg-paper/12 border-paper/35 text-paper',
  busy: 'bg-paper/8 border-paper/20 text-paper/85',
}

export function EventChip({
  event,
  timeZone,
  compact = false,
}: {
  event: CalendarEvent
  timeZone: string
  compact?: boolean
}) {
  const free = event.transparency === 'free'
  return (
    <div
      className={[
        'h-full overflow-hidden rounded-md border-l-3 px-1.5 py-1 text-left',
        TONE[event.kind],
        free && 'opacity-60 border-dashed',
      ]
        .filter(Boolean)
        .join(' ')}
      title={event.title}
    >
      <p className="truncate text-xs font-medium leading-tight">{event.title}</p>
      {!compact && (
        <p className="truncate text-[0.6875rem] text-muted">
          {event.all_day ? 'All day' : formatTime(new Date(event.start), timeZone)}
          {event.location ? ` · ${event.location}` : ''}
        </p>
      )}
    </div>
  )
}
