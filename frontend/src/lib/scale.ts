/**
 * Minutes to CSS length.
 *
 * Every vertical position in the calendar derives from `--spacing-hour`, so
 * changing the zoom level is a one-line theme change rather than a sweep
 * through the components.
 */
export function minutesToLength(minutes: number): string {
  return `calc(${minutes} * (var(--spacing-hour) / 60))`
}
