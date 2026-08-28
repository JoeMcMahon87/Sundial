// Timezone arithmetic for the calendar views.
//
// The scheduler reasons in the user's *current* zone (§6.7), so every
// conversion here takes an IANA zone rather than leaning on the host's local
// time. `Intl` is the only timezone database available in the browser, so the
// offset lookups go through it rather than through a hand-rolled table.

const PARTS = new Map<string, Intl.DateTimeFormat>()

function formatter(timeZone: string): Intl.DateTimeFormat {
  let cached = PARTS.get(timeZone)
  if (!cached) {
    cached = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    PARTS.set(timeZone, cached)
  }
  return cached
}

interface Wall {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  second: number
}

function wallClock(instant: Date, timeZone: string): Wall {
  const found: Record<string, number> = {}
  for (const part of formatter(timeZone).formatToParts(instant)) {
    if (part.type !== 'literal') found[part.type] = Number(part.value)
  }
  // Intl renders midnight as hour 24 in some engines; 24:00 is the same
  // instant as 00:00 on the following day, and the day field is already right.
  return {
    year: found.year!,
    month: found.month!,
    day: found.day!,
    hour: found.hour! % 24,
    minute: found.minute!,
    second: found.second!,
  }
}

/** The zone's offset from UTC, in milliseconds, at a given instant. */
function offsetMs(instant: Date, timeZone: string): number {
  const w = wallClock(instant, timeZone)
  const asIfUtc = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second)
  return asIfUtc - Math.floor(instant.getTime() / 1000) * 1000
}

/**
 * The instant at which the given wall-clock time occurs in `timeZone`.
 *
 * Two passes, because the offset depends on the answer: the first guess uses
 * the offset at the *wrong* instant, and the second corrects it. When the two
 * offsets agree, that is the answer, and an ambiguous fall-back time resolves
 * to its first occurrence.
 *
 * When they disagree the requested time may not exist at all — 02:30 on a
 * spring-forward morning. Such a time is shifted *forward* past the gap, never
 * backward. This is not cosmetic: `startOfDay` asks for local midnight, and
 * some zones transition at exactly midnight (America/Santiago), so shifting
 * backward would land the start of a day in the previous day and take
 * `dateKeyOf(startOfDay(k)) === k` with it.
 */
export function zonedTimeToUtc(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  timeZone: string,
): Date {
  const guess = Date.UTC(year, month - 1, day, hour, minute)
  const first = offsetMs(new Date(guess), timeZone)
  const second = offsetMs(new Date(guess - first), timeZone)
  if (first === second) return new Date(guess - first)

  const candidate = new Date(guess - second)
  const wall = wallClock(candidate, timeZone)
  if (wall.hour === hour && wall.minute === minute && wall.day === day) return candidate

  // The gap. A clock only ever jumps forward, so the smaller offset is the
  // pre-transition one and subtracting it steps over the missing hour.
  return new Date(guess - Math.min(first, second))
}

/** `YYYY-MM-DD` — the local calendar date of an instant. */
export function dateKeyOf(instant: Date, timeZone: string): string {
  const w = wallClock(instant, timeZone)
  return `${w.year}-${pad(w.month)}-${pad(w.day)}`
}

export function startOfDay(dateKey: string, timeZone: string): Date {
  const [year, month, day] = dateKey.split('-').map(Number) as [number, number, number]
  return zonedTimeToUtc(year, month, day, 0, 0, timeZone)
}

/** Pure calendar arithmetic on a date key — no instants, so no DST involved. */
export function addDays(dateKey: string, days: number): string {
  const [year, month, day] = dateKey.split('-').map(Number) as [number, number, number]
  const moved = new Date(Date.UTC(year, month - 1, day + days))
  return `${moved.getUTCFullYear()}-${pad(moved.getUTCMonth() + 1)}-${pad(moved.getUTCDate())}`
}

/** Monday-based, matching the week grid in §10.2. */
export function startOfWeek(dateKey: string): string {
  const [year, month, day] = dateKey.split('-').map(Number) as [number, number, number]
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay()
  return addDays(dateKey, weekday === 0 ? -6 : 1 - weekday)
}

export function weekDays(dateKey: string): string[] {
  const monday = startOfWeek(dateKey)
  return Array.from({ length: 7 }, (_, index) => addDays(monday, index))
}

/**
 * How long the local day actually is.
 *
 * 1440 on most days, 1380 on a spring-forward day and 1500 on a fall-back one.
 * Positioning a timeline against a hardcoded 1440 puts every event after the
 * transition in the wrong place, by exactly an hour, twice a year.
 */
export function dayLengthMinutes(dateKey: string, timeZone: string): number {
  const from = startOfDay(dateKey, timeZone)
  const to = startOfDay(addDays(dateKey, 1), timeZone)
  return (to.getTime() - from.getTime()) / 60_000
}

/** Minutes elapsed since local midnight — the timeline's y-axis. */
export function minutesIntoDay(instant: Date, dateKey: string, timeZone: string): number {
  return (instant.getTime() - startOfDay(dateKey, timeZone).getTime()) / 60_000
}

export function formatTime(instant: Date, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    hour: 'numeric',
    minute: '2-digit',
  }).format(instant)
}

/** Just the hour, for the timeline gutter — ":00" on every row is noise. */
export function formatHour(instant: Date, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone, hour: 'numeric' }).format(instant)
}

export function formatDayName(dateKey: string, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone, weekday: 'short' }).format(
    startOfDay(dateKey, timeZone),
  )
}

export function formatDayLong(dateKey: string, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  }).format(startOfDay(dateKey, timeZone))
}

export function dayOfMonth(dateKey: string): number {
  return Number(dateKey.slice(8, 10))
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}
