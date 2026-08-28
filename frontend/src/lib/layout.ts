// Turning events into rectangles.

import type { CalendarEvent } from '../api'
import { addDays, dateKeyOf, dayLengthMinutes, minutesIntoDay, startOfDay } from './time'

export interface Placed {
  event: CalendarEvent
  /** Minutes from local midnight, clamped to the day. */
  top: number
  /** Height in minutes; never less than `MIN_VISIBLE_MINUTES`. */
  height: number
  /** Which of `lanes` this event occupies, for side-by-side overlaps. */
  lane: number
  lanes: number
}

/** A 5-minute meeting still has to be tappable (§10.3). */
export const MIN_VISIBLE_MINUTES = 20

export function isAllDay(event: CalendarEvent): boolean {
  return event.all_day
}

/** Whether a timed event intersects the given local day at all. */
export function occursOn(event: CalendarEvent, dateKey: string, timeZone: string): boolean {
  if (event.all_day) {
    // Google's all-day end date is exclusive.
    return event.start <= dateKey && dateKey < event.end
  }
  const dayStart = startOfDay(dateKey, timeZone).getTime()
  const dayEnd = startOfDay(addDays(dateKey, 1), timeZone).getTime()
  return new Date(event.start).getTime() < dayEnd && new Date(event.end).getTime() > dayStart
}

/**
 * Position timed events within one local day.
 *
 * Everything is measured in minutes-from-local-midnight rather than in hours,
 * because a spring-forward day is 23 hours long and a fall-back day is 25
 * (§6.7). Anything laid out against a hardcoded 1440 is an hour out, twice a
 * year, for half the events on the screen.
 */
export function layOutDay(
  events: CalendarEvent[],
  dateKey: string,
  timeZone: string,
): { timed: Placed[]; allDay: CalendarEvent[] } {
  const dayMinutes = dayLengthMinutes(dateKey, timeZone)
  const onThisDay = events.filter((event) => occursOn(event, dateKey, timeZone))

  const allDay = onThisDay.filter(isAllDay)
  const timed = onThisDay
    .filter((event) => !isAllDay(event))
    .map((event) => {
      const rawTop = minutesIntoDay(new Date(event.start), dateKey, timeZone)
      const rawEnd = minutesIntoDay(new Date(event.end), dateKey, timeZone)
      const top = clamp(rawTop, 0, dayMinutes)
      const end = clamp(rawEnd, 0, dayMinutes)
      return {
        event,
        top,
        height: Math.max(end - top, MIN_VISIBLE_MINUTES),
        lane: 0,
        lanes: 1,
      }
    })
    .sort((a, b) => a.top - b.top || a.height - b.height)

  return { timed: assignLanes(timed), allDay }
}

/**
 * Spread overlapping events across columns.
 *
 * Events are grouped into runs that transitively overlap, and every event in a
 * run shares a lane count so their widths line up. Doing this per-event
 * instead produces columns that jump width halfway down a cluster.
 */
function assignLanes(placed: Placed[]): Placed[] {
  const result: Placed[] = []
  let cluster: Placed[] = []
  let clusterEnd = -Infinity

  const flush = () => {
    if (cluster.length === 0) return
    const laneEnds: number[] = []
    for (const item of cluster) {
      let lane = laneEnds.findIndex((end) => end <= item.top)
      if (lane === -1) {
        lane = laneEnds.length
        laneEnds.push(0)
      }
      laneEnds[lane] = item.top + item.height
      item.lane = lane
    }
    for (const item of cluster) item.lanes = laneEnds.length
    result.push(...cluster)
    cluster = []
    clusterEnd = -Infinity
  }

  for (const item of placed) {
    if (item.top >= clusterEnd) flush()
    cluster.push(item)
    clusterEnd = Math.max(clusterEnd, item.top + item.height)
  }
  flush()
  return result
}

/** Minutes from local midnight to now, or null when `dateKey` is not today. */
export function nowOffset(dateKey: string, timeZone: string): number | null {
  const now = new Date()
  if (dateKeyOf(now, timeZone) !== dateKey) return null
  return minutesIntoDay(now, dateKey, timeZone)
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high)
}
