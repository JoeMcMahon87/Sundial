import type { CalendarEvent } from '../api'
import { layOutDay } from '../lib/layout'
import { dateKeyOf, dayLengthMinutes, dayOfMonth, formatDayName, weekDays } from '../lib/time'
import { AllDayStrip } from './AllDayStrip'
import { minutesToLength } from '../lib/scale'
import { DayColumn } from './DayColumn'
import { HourGutter, HourLines } from './HourGutter'

/**
 * §10.2: a 7-day grid. §10.3: below 768px it becomes a 3-day horizontal
 * scroll, which is done here with a min-width per column plus `snap` rather
 * than with a second component — one layout, two shapes.
 */
export function WeekView({
  events,
  anchor,
  timeZone,
}: {
  events: CalendarEvent[]
  anchor: string
  timeZone: string
}) {
  const days = weekDays(anchor)
  const today = dateKeyOf(new Date(), timeZone)

  // The grid needs one height. Days differ in length twice a year, so take the
  // longest in view; a 23-hour day then simply ends an hour early.
  const hours = Math.max(...days.map((day) => dayLengthMinutes(day, timeZone) / 60))

  return (
    <section aria-label={`Week of ${days[0]}`} className="flex min-h-0 flex-col">
      <div className="flex overflow-x-auto">
        <div className="w-12 shrink-0" />
        <div className="flex grow snap-x snap-mandatory">
          {days.map((day) => (
            <div
              key={day}
              className="min-w-[min(33vw,10rem)] flex-1 snap-start px-1 md:min-w-0"
            >
              <div className="pb-1 text-center">
                <p className="text-[0.6875rem] uppercase tracking-wide text-muted">
                  {formatDayName(day, timeZone)}
                </p>
                <p
                  className={[
                    'mx-auto grid size-7 place-items-center rounded-full text-sm tabular-nums',
                    day === today ? 'bg-accent font-semibold text-ink' : 'text-paper',
                  ].join(' ')}
                >
                  {dayOfMonth(day)}
                </p>
              </div>
              <AllDayStrip
                events={layOutDay(events, day, timeZone).allDay}
                timeZone={timeZone}
              />
            </div>
          ))}
        </div>
      </div>

      <div className="min-h-0 grow overflow-auto">
        <div className="relative flex" style={{ height: minutesToLength(hours * 60) }}>
          <HourGutter dateKey={days[0]!} timeZone={timeZone} hours={hours} />
          <div className="relative flex grow snap-x snap-mandatory">
            <HourLines hours={hours} />
            {days.map((day) => (
              <div
                key={day}
                className="min-w-[min(33vw,10rem)] flex-1 snap-start border-l border-paper/8 px-0.5 md:min-w-0"
              >
                <DayColumn events={events} dateKey={day} timeZone={timeZone} compact />
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
