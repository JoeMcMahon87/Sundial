import { describe, expect, it } from 'vitest'
import type { CalendarEvent } from '../api'
import { layOutDay, nowOffset, occursOn } from './layout'

const LA = 'America/Los_Angeles'

function timed(start: string, end: string, title = 'Event'): CalendarEvent {
  return {
    event_id: `${title}-${start}`,
    origin: 'google',
    kind: 'appointment',
    title,
    start,
    end,
    all_day: false,
    tz: LA,
    location: null,
    transparency: 'busy',
    locked: false,
    task_id: null,
    calendar_id: 'primary',
  }
}

function allDay(start: string, end: string, title = 'All day'): CalendarEvent {
  return { ...timed(start, end, title), all_day: true }
}

describe('occursOn', () => {
  it('includes a timed event inside the day', () => {
    expect(occursOn(timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z'), '2026-08-28', LA)).toBe(
      true,
    )
  })

  it('includes an event that straddles midnight', () => {
    const overnight = timed('2026-08-28T04:00:00Z', '2026-08-28T09:00:00Z')
    expect(occursOn(overnight, '2026-08-27', LA)).toBe(true)
    expect(occursOn(overnight, '2026-08-28', LA)).toBe(true)
  })

  it('excludes a neighbouring day', () => {
    expect(occursOn(timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z'), '2026-08-29', LA)).toBe(
      false,
    )
  })

  it('treats an all-day end date as exclusive', () => {
    const conference = allDay('2026-08-28', '2026-08-30')
    expect(occursOn(conference, '2026-08-28', LA)).toBe(true)
    expect(occursOn(conference, '2026-08-29', LA)).toBe(true)
    expect(occursOn(conference, '2026-08-30', LA)).toBe(false)
  })
})

describe('layOutDay', () => {
  it('separates all-day events from timed ones', () => {
    const { timed: placed, allDay: banner } = layOutDay(
      [
        allDay('2026-08-28', '2026-08-29'),
        timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z'),
      ],
      '2026-08-28',
      LA,
    )
    expect(placed).toHaveLength(1)
    expect(banner).toHaveLength(1)
  })

  it('positions an event by minutes from local midnight', () => {
    // 09:00 Pacific on an ordinary day.
    const { timed: placed } = layOutDay(
      [timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z')],
      '2026-08-28',
      LA,
    )
    expect(placed[0]!.top).toBe(9 * 60)
    expect(placed[0]!.height).toBe(60)
  })

  it('positions events correctly on a 23-hour day', () => {
    // 16:00Z is 09:00 local, the clock having already jumped to PDT. Only
    // eight hours have elapsed since local midnight because 02:00 never
    // happened, so a layout measuring in wall-clock hours is an hour out.
    const { timed: placed } = layOutDay(
      [timed('2026-03-08T16:00:00Z', '2026-03-08T17:00:00Z')],
      '2026-03-08',
      LA,
    )
    expect(placed[0]!.top).toBe(8 * 60)
  })

  it('spans the full 23 hours of a spring-forward day', () => {
    const lateNight = timed('2026-03-09T06:00:00Z', '2026-03-09T06:30:00Z')
    const { timed: placed } = layOutDay([lateNight], '2026-03-08', LA)
    expect(placed[0]!.top).toBe(22 * 60)
  })

  it('clamps an event that starts before the day begins', () => {
    const overnight = timed('2026-08-28T04:00:00Z', '2026-08-28T09:00:00Z')
    const { timed: placed } = layOutDay([overnight], '2026-08-28', LA)
    expect(placed[0]!.top).toBe(0)
    expect(placed[0]!.height).toBe(2 * 60)
  })

  it('gives a very short event a tappable height', () => {
    const { timed: placed } = layOutDay(
      [timed('2026-08-28T16:00:00Z', '2026-08-28T16:05:00Z')],
      '2026-08-28',
      LA,
    )
    expect(placed[0]!.height).toBeGreaterThanOrEqual(20)
  })

  it('leaves non-overlapping events in a single lane', () => {
    const { timed: placed } = layOutDay(
      [
        timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z', 'First'),
        timed('2026-08-28T18:00:00Z', '2026-08-28T19:00:00Z', 'Second'),
      ],
      '2026-08-28',
      LA,
    )
    expect(placed.map((p) => p.lanes)).toEqual([1, 1])
  })

  it('splits two overlapping events into two lanes', () => {
    const { timed: placed } = layOutDay(
      [
        timed('2026-08-28T16:00:00Z', '2026-08-28T17:30:00Z', 'First'),
        timed('2026-08-28T17:00:00Z', '2026-08-28T18:00:00Z', 'Second'),
      ],
      '2026-08-28',
      LA,
    )
    expect(placed.map((p) => p.lane)).toEqual([0, 1])
    expect(placed.map((p) => p.lanes)).toEqual([2, 2])
  })

  it('gives every event in an overlapping run the same width', () => {
    // Otherwise a column changes width halfway down a cluster, which reads as
    // a rendering bug.
    const { timed: placed } = layOutDay(
      [
        timed('2026-08-28T16:00:00Z', '2026-08-28T19:00:00Z', 'Long'),
        timed('2026-08-28T16:30:00Z', '2026-08-28T17:00:00Z', 'Short A'),
        timed('2026-08-28T17:30:00Z', '2026-08-28T18:00:00Z', 'Short B'),
      ],
      '2026-08-28',
      LA,
    )
    expect(new Set(placed.map((p) => p.lanes))).toEqual(new Set([2]))
  })

  it('reuses a lane once the earlier event has finished', () => {
    const { timed: placed } = layOutDay(
      [
        timed('2026-08-28T16:00:00Z', '2026-08-28T20:00:00Z', 'All morning'),
        timed('2026-08-28T16:30:00Z', '2026-08-28T17:00:00Z', 'A'),
        timed('2026-08-28T18:00:00Z', '2026-08-28T18:30:00Z', 'B'),
      ],
      '2026-08-28',
      LA,
    )
    const byTitle = Object.fromEntries(placed.map((p) => [p.event.title, p.lane]))
    expect(byTitle['A']).toBe(1)
    expect(byTitle['B']).toBe(1)
  })
})

describe('nowOffset', () => {
  it('is null for a day that is not today', () => {
    expect(nowOffset('1999-01-01', LA)).toBeNull()
  })

  it('is a minute count for today', () => {
    const today = new Intl.DateTimeFormat('en-CA', { timeZone: LA }).format(new Date())
    const offset = nowOffset(today, LA)
    expect(offset).not.toBeNull()
    expect(offset!).toBeGreaterThanOrEqual(0)
    expect(offset!).toBeLessThanOrEqual(25 * 60)
  })
})
