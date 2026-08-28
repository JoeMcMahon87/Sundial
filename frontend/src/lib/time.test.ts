import { describe, expect, it } from 'vitest'
import {
  addDays,
  dateKeyOf,
  dayLengthMinutes,
  minutesIntoDay,
  startOfDay,
  startOfWeek,
  weekDays,
  zonedTimeToUtc,
} from './time'

const LA = 'America/Los_Angeles'
const TOKYO = 'Asia/Tokyo'

describe('startOfDay', () => {
  it('resolves local midnight to the right instant west of UTC', () => {
    expect(startOfDay('2026-08-28', LA).toISOString()).toBe('2026-08-28T07:00:00.000Z')
  })

  it('resolves local midnight to the right instant east of UTC', () => {
    expect(startOfDay('2026-08-28', TOKYO).toISOString()).toBe('2026-08-27T15:00:00.000Z')
  })

  it('is the identity in UTC', () => {
    expect(startOfDay('2026-08-28', 'UTC').toISOString()).toBe('2026-08-28T00:00:00.000Z')
  })
})

describe('daylight saving', () => {
  // 2026-03-08 is the US spring-forward day: 02:00 local never happens.
  // 2026-11-01 is fall-back: 01:00 local happens twice.
  it('reports a 23-hour spring-forward day', () => {
    expect(dayLengthMinutes('2026-03-08', LA)).toBe(23 * 60)
  })

  it('reports a 25-hour fall-back day', () => {
    expect(dayLengthMinutes('2026-11-01', LA)).toBe(25 * 60)
  })

  it('reports 24 hours on an ordinary day', () => {
    expect(dayLengthMinutes('2026-08-28', LA)).toBe(24 * 60)
  })

  it('never sees a transition in a zone without one', () => {
    expect(dayLengthMinutes('2026-03-08', TOKYO)).toBe(24 * 60)
    expect(dayLengthMinutes('2026-11-01', TOKYO)).toBe(24 * 60)
  })

  it('places 09:00 at 480 minutes on a spring-forward day, not 540', () => {
    // The hour from 02:00 to 03:00 does not exist, so 09:00 local is only
    // eight elapsed hours after midnight. Against a hardcoded 1440-minute day
    // every event after the transition renders an hour out.
    const nineAm = zonedTimeToUtc(2026, 3, 8, 9, 0, LA)
    expect(minutesIntoDay(nineAm, '2026-03-08', LA)).toBe(8 * 60)
  })

  it('places 09:00 at 600 minutes on a fall-back day', () => {
    const nineAm = zonedTimeToUtc(2026, 11, 1, 9, 0, LA)
    expect(minutesIntoDay(nineAm, '2026-11-01', LA)).toBe(10 * 60)
  })

  it('shifts a wall-clock time that does not exist forward past the gap', () => {
    // 02:30 on spring-forward morning never happens. Shifting forward lands on
    // 03:30 local; shifting backward would land on the previous hour, which is
    // what breaks midnight-transition zones.
    const impossible = zonedTimeToUtc(2026, 3, 8, 2, 30, LA)
    expect(dateKeyOf(impossible, LA)).toBe('2026-03-08')
    expect(minutesIntoDay(impossible, '2026-03-08', LA)).toBe(150)
  })

  it('resolves an ambiguous fall-back time to its first occurrence', () => {
    // 01:30 happens twice on 2026-11-01 in Los Angeles.
    const ambiguous = zonedTimeToUtc(2026, 11, 1, 1, 30, LA)
    expect(ambiguous.toISOString()).toBe('2026-11-01T08:30:00.000Z')
  })

  it('keeps startOfDay inside its own day where the zone shifts at midnight', () => {
    // America/Santiago moves the clock at midnight, so local 00:00 does not
    // exist on the transition day. This is the case that makes the forward
    // shift load-bearing rather than academic.
    const santiago = 'America/Santiago'
    for (const key of ['2026-09-05', '2026-09-06', '2026-09-07', '2026-04-04', '2026-04-05']) {
      expect(dateKeyOf(startOfDay(key, santiago), santiago)).toBe(key)
    }
  })
})

describe('dateKeyOf', () => {
  it('uses the local date, not the UTC one', () => {
    const lateEvening = new Date('2026-08-29T05:00:00Z')
    expect(dateKeyOf(lateEvening, LA)).toBe('2026-08-28')
    expect(dateKeyOf(lateEvening, 'UTC')).toBe('2026-08-29')
    expect(dateKeyOf(lateEvening, TOKYO)).toBe('2026-08-29')
  })

  it('round-trips with startOfDay', () => {
    for (const key of ['2026-03-08', '2026-11-01', '2026-08-28', '2027-01-01']) {
      expect(dateKeyOf(startOfDay(key, LA), LA)).toBe(key)
    }
  })
})

describe('addDays', () => {
  it('crosses month and year boundaries', () => {
    expect(addDays('2026-08-31', 1)).toBe('2026-09-01')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31')
  })

  it('handles a leap day', () => {
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29')
    expect(addDays('2028-02-29', 1)).toBe('2028-03-01')
  })

  it('steps over a DST boundary without drifting', () => {
    expect(addDays('2026-03-07', 1)).toBe('2026-03-08')
    expect(addDays('2026-03-08', 1)).toBe('2026-03-09')
  })
})

describe('startOfWeek', () => {
  it('is Monday-based', () => {
    expect(startOfWeek('2026-08-28')).toBe('2026-08-24') // Friday -> Monday
    expect(startOfWeek('2026-08-24')).toBe('2026-08-24') // Monday -> itself
  })

  it('treats Sunday as the end of the week, not the start', () => {
    expect(startOfWeek('2026-08-30')).toBe('2026-08-24')
  })

  it('yields seven consecutive days', () => {
    expect(weekDays('2026-08-28')).toEqual([
      '2026-08-24',
      '2026-08-25',
      '2026-08-26',
      '2026-08-27',
      '2026-08-28',
      '2026-08-29',
      '2026-08-30',
    ])
  })
})
