import { create } from 'zustand'
import { addDays, browserTimeZone, dateKeyOf } from './lib/time'

export type View = 'today' | 'week'

interface UiState {
  view: View
  /** The day Today shows, and the week Week shows — a `YYYY-MM-DD` key. */
  anchor: string
  /**
   * The user's *current* zone (§6.7). Read from the browser until M2 puts it
   * on the policy document, at which point this reads from there instead.
   */
  timeZone: string
  setView: (view: View) => void
  setAnchor: (anchor: string) => void
  shift: (days: number) => void
  today: () => void
}

export const useUi = create<UiState>((set, get) => {
  const timeZone = browserTimeZone()
  return {
    view: 'today',
    anchor: dateKeyOf(new Date(), timeZone),
    timeZone,
    setView: (view) => set({ view }),
    setAnchor: (anchor) => set({ anchor }),
    shift: (days) => set({ anchor: addDays(get().anchor, days) }),
    today: () => set({ anchor: dateKeyOf(new Date(), get().timeZone) }),
  }
})
