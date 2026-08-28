import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CalendarEvent } from '../api'
import { TodayView } from './TodayView'
import { WeekView } from './WeekView'

const LA = 'America/Los_Angeles'
const DAY = '2026-08-28' // a Friday

function timed(start: string, end: string, title: string): CalendarEvent {
  return {
    event_id: title,
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

function allDay(start: string, end: string, title: string): CalendarEvent {
  return { ...timed(start, end, title), all_day: true }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('TodayView', () => {
  it('renders the events on that day', () => {
    render(
      <TodayView
        events={[timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z', 'Standup')]}
        dateKey={DAY}
        timeZone={LA}
      />,
    )
    expect(screen.getByText('Standup')).toBeDefined()
  })

  it('omits events belonging to another day', () => {
    render(
      <TodayView
        events={[timed('2026-08-30T16:00:00Z', '2026-08-30T17:00:00Z', 'Sunday thing')]}
        dateKey={DAY}
        timeZone={LA}
      />,
    )
    expect(screen.queryByText('Sunday thing')).toBeNull()
  })

  it('puts an all-day event in the strip, not in the timeline', () => {
    render(
      <TodayView
        events={[
          allDay('2026-08-28', '2026-08-29', 'Conference'),
          timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z', 'Standup'),
        ]}
        dateKey={DAY}
        timeZone={LA}
      />,
    )
    const strip = screen.getByLabelText('All-day events')
    expect(within(strip).getByText('Conference')).toBeDefined()
    // An all-day event has no position on a clock, so it must not also appear
    // in the timeline.
    expect(within(strip).queryByText('Standup')).toBeNull()
    expect(screen.getAllByText('Conference')).toHaveLength(1)
  })

  it('draws a now-line only when the day on screen is today', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-28T19:00:00Z'))

    const { rerender } = render(<TodayView events={[]} dateKey={DAY} timeZone={LA} />)
    expect(screen.queryByLabelText('Now')).not.toBeNull()

    rerender(<TodayView events={[]} dateKey="2026-08-29" timeZone={LA} />)
    expect(screen.queryByLabelText('Now')).toBeNull()
  })

  it('labels hours in local time and skips the hour DST removes', () => {
    // Labels are derived from independently chosen instants, so this is not
    // the component checking its own arithmetic: on 2026-03-08 in Los Angeles
    // 09:00Z is 01:00 local and 10:00Z is 03:00 local, because the clock jumps
    // straight over 02:00.
    const hour = new Intl.DateTimeFormat(undefined, { timeZone: LA, hour: 'numeric' })
    const oneAm = hour.format(new Date('2026-03-08T09:00:00Z'))
    const threeAm = hour.format(new Date('2026-03-08T10:00:00Z'))

    render(<TodayView events={[]} dateKey="2026-03-08" timeZone={LA} />)
    const gutter = screen.getByLabelText('Hours')

    expect(within(gutter).getByText(oneAm)).toBeDefined()
    expect(within(gutter).getByText(threeAm)).toBeDefined()
    expect(oneAm).not.toBe(threeAm)

    // A 23-hour day has 23 rows, the first of which is deliberately blank.
    expect(gutter.children).toHaveLength(23)
  })
})

describe('WeekView', () => {
  it('renders seven day headings', () => {
    render(<WeekView events={[]} anchor={DAY} timeZone={LA} />)
    const week = screen.getByLabelText('Week of 2026-08-24')
    // Monday the 24th through Sunday the 30th.
    for (const date of ['24', '25', '26', '27', '28', '29', '30']) {
      expect(within(week).getByText(date)).toBeDefined()
    }
  })

  it('places each event in its own day', () => {
    render(
      <WeekView
        events={[
          timed('2026-08-25T16:00:00Z', '2026-08-25T17:00:00Z', 'Tuesday'),
          timed('2026-08-28T16:00:00Z', '2026-08-28T17:00:00Z', 'Friday'),
        ]}
        anchor={DAY}
        timeZone={LA}
      />,
    )
    expect(screen.getByText('Tuesday')).toBeDefined()
    expect(screen.getByText('Friday')).toBeDefined()
  })

  it('shows the whole week regardless of which day inside it is the anchor', () => {
    const sunday = timed('2026-08-31T02:00:00Z', '2026-08-31T03:00:00Z', 'Sunday evening')
    render(<WeekView events={[sunday]} anchor="2026-08-24" timeZone={LA} />)
    expect(screen.getByText('Sunday evening')).toBeDefined()
  })
})
