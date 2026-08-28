import { useEffect, useRef } from 'react'
import type { CalendarEvent } from '../api'
import { layOutDay, nowOffset } from '../lib/layout'
import { dayLengthMinutes, formatDayLong } from '../lib/time'
import { AllDayStrip } from './AllDayStrip'
import { minutesToLength } from '../lib/scale'
import { DayColumn } from './DayColumn'
import { HourGutter, HourLines } from './HourGutter'

/** §10.2: a vertical timeline of the day, with a now-line. */
export function TodayView({
  events,
  dateKey,
  timeZone,
}: {
  events: CalendarEvent[]
  dateKey: string
  timeZone: string
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const { allDay } = layOutDay(events, dateKey, timeZone)
  const hours = dayLengthMinutes(dateKey, timeZone) / 60

  // Open on something worth seeing: the now-line if this is today, otherwise
  // the first event. Landing on 00:00 every time makes the view feel broken.
  useEffect(() => {
    const now = nowOffset(dateKey, timeZone)
    const { timed } = layOutDay(events, dateKey, timeZone)
    const target = now ?? timed[0]?.top ?? 8 * 60
    const element = scroller.current
    if (!element) return
    const perMinute = element.scrollHeight / (hours * 60)
    element.scrollTop = Math.max(0, (target - 60) * perMinute)
  }, [dateKey, timeZone, events, hours])

  return (
    <section aria-label={formatDayLong(dateKey, timeZone)} className="flex min-h-0 flex-col">
      <AllDayStrip events={allDay} timeZone={timeZone} />

      <div ref={scroller} className="min-h-0 grow overflow-y-auto">
        <div className="relative flex" style={{ height: minutesToLength(hours * 60) }}>
          <HourGutter dateKey={dateKey} timeZone={timeZone} hours={hours} />
          <div className="relative grow pr-1">
            <HourLines hours={hours} />
            <DayColumn events={events} dateKey={dateKey} timeZone={timeZone} />
          </div>
        </div>
      </div>
    </section>
  )
}
