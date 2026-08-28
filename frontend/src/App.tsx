import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiError, api, type Me } from './api'
import { Header, TabBar } from './components/Chrome'
import { TodayView } from './components/TodayView'
import { WeekView } from './components/WeekView'
import { useConnection } from './hooks/useConnection'
import { useEvents } from './hooks/useEvents'
import { weekDays } from './lib/time'
import { useUi } from './store'
import './styles.css'

const queryClient = new QueryClient()

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  )
}

function Shell() {
  const { state, refresh } = useConnection()

  if (state.status === 'loading') {
    return <Centered>Checking…</Centered>
  }

  if (state.status === 'signed-out') {
    return (
      <Centered>
        <p className="text-muted">Not signed in.</p>
        <a
          className="rounded-lg bg-accent px-4 py-2.5 font-semibold text-ink"
          href={api.loginUrl}
        >
          Sign in with Google
        </a>
      </Centered>
    )
  }

  if (state.status === 'error') {
    return (
      <Centered>
        <p role="alert" className="text-danger">
          {state.message}
        </p>
      </Centered>
    )
  }

  if (state.me.connection.state !== 'connected') {
    return <Reconnect me={state.me} />
  }

  return <Calendar onSignOut={() => void api.logout().then(refresh)} />
}

function Calendar({ onSignOut }: { onSignOut: () => void }) {
  const { view, anchor, timeZone, setView, shift, today } = useUi()

  // Today fetches one day, Week fetches seven — the query key covers both, so
  // switching views reuses whatever is already cached.
  const week = weekDays(anchor)
  const [firstDay, lastDay] =
    view === 'today' ? [anchor, anchor] : [week[0] ?? anchor, week[6] ?? anchor]
  const events = useEvents(firstDay, lastDay, timeZone)

  return (
    <div className="flex h-dvh flex-col">
      <Header
        view={view}
        anchor={anchor}
        timeZone={timeZone}
        onShift={shift}
        onToday={today}
      />

      <main className="flex min-h-0 grow flex-col px-3">
        {events.isPending && <p className="p-4 text-muted">Loading your calendar…</p>}

        {events.isError && (
          <p role="alert" className="p-4 text-danger">
            Could not load events. {describe(events.error)}
          </p>
        )}

        {events.data &&
          (view === 'today' ? (
            <TodayView events={events.data} dateKey={anchor} timeZone={timeZone} />
          ) : (
            <WeekView events={events.data} anchor={anchor} timeZone={timeZone} />
          ))}
      </main>

      <TabBar view={view} onChange={setView} />

      <button
        type="button"
        onClick={onSignOut}
        className="absolute right-3 top-2 text-xs text-muted md:static md:self-end"
      >
        Sign out
      </button>
    </div>
  )
}

function Reconnect({ me }: { me: Me }) {
  const needsReconnect = me.connection.state === 'needs_reconnect'
  return (
    <Centered>
      <p className="text-muted">
        {needsReconnect
          ? 'Sundial lost access to your Google account.'
          : 'Connect a Google account to see your calendar.'}
      </p>
      <a className="rounded-lg bg-accent px-4 py-2.5 font-semibold text-ink" href={api.loginUrl}>
        {needsReconnect ? 'Reconnect Google' : 'Connect Google'}
      </a>
    </Centered>
  )
}

/** Prefer the problem+json `detail`, which is the sentence written for a human. */
function describe(error: Error): string {
  return error instanceof ApiError ? (error.problem.detail ?? error.problem.title) : error.message
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto flex h-dvh max-w-lg flex-col items-start justify-center gap-3 px-5">
      <h1 className="text-xl font-semibold tracking-tight">Sundial</h1>
      {children}
    </main>
  )
}
