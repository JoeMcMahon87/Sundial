import type { View } from '../store'
import { formatDayLong, weekDays } from '../lib/time'

const TABS: { id: View; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Week' },
]

export function Header({
  view,
  anchor,
  timeZone,
  onShift,
  onToday,
}: {
  view: View
  anchor: string
  timeZone: string
  onShift: (days: number) => void
  onToday: () => void
}) {
  const days = weekDays(anchor)
  const title =
    view === 'today'
      ? formatDayLong(anchor, timeZone)
      : `${formatDayLong(days[0]!, timeZone)} – ${formatDayLong(days[6]!, timeZone)}`

  return (
    <header className="flex items-center gap-2 border-b border-paper/10 px-3 py-2">
      <h1 className="min-w-0 grow truncate text-base font-semibold">{title}</h1>

      <nav className="flex items-center gap-1" aria-label="Change date">
        <button
          type="button"
          className="grid size-11 place-items-center rounded-md text-muted hover:text-paper"
          onClick={() => onShift(view === 'today' ? -1 : -7)}
          aria-label={view === 'today' ? 'Previous day' : 'Previous week'}
        >
          ‹
        </button>
        <button
          type="button"
          className="rounded-md px-2 text-sm text-muted hover:text-paper"
          onClick={onToday}
        >
          Today
        </button>
        <button
          type="button"
          className="grid size-11 place-items-center rounded-md text-muted hover:text-paper"
          onClick={() => onShift(view === 'today' ? 1 : 7)}
          aria-label={view === 'today' ? 'Next day' : 'Next week'}
        >
          ›
        </button>
      </nav>
    </header>
  )
}

/**
 * §10.3's bottom tab bar. Tasks, Inbox and More arrive with M2 and M5; listing
 * them now as dead tabs would be worse than showing what exists.
 */
export function TabBar({
  view,
  onChange,
}: {
  view: View
  onChange: (view: View) => void
}) {
  return (
    <nav
      className="flex shrink-0 border-t border-paper/10 pb-[env(safe-area-inset-bottom)]"
      aria-label="Views"
    >
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          onClick={() => onChange(tab.id)}
          aria-current={view === tab.id ? 'page' : undefined}
          className={[
            'grow py-2 text-sm',
            view === tab.id ? 'font-semibold text-accent' : 'text-muted',
          ].join(' ')}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  )
}
