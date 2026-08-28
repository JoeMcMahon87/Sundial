import { api, type Me } from './api'
import { useConnection } from './useConnection'
import './styles.css'

// The M0 shell: sign in, and see whether Google is connected. The six real
// screens (§10.2) arrive from M1 onward.
export default function App() {
  const { state, refresh } = useConnection()

  return (
    <main>
      <h1>Sundial</h1>

      {state.status === 'loading' && <p className="muted">Checking…</p>}

      {state.status === 'signed-out' && (
        <>
          <p className="muted">Not signed in.</p>
          <a className="button" href={api.loginUrl}>
            Sign in with Google
          </a>
        </>
      )}

      {state.status === 'error' && (
        <p role="alert" className="error">
          {state.message}
        </p>
      )}

      {state.status === 'ready' && <Connected me={state.me} onChange={refresh} />}
    </main>
  )
}

function Connected({ me, onChange }: { me: Me; onChange: () => Promise<void> }) {
  const { connection } = me

  return (
    <>
      <p>
        <span className={`dot dot-${connection.state}`} aria-hidden="true" />
        Google: <strong>{connection.state.replace('_', ' ')}</strong>
        {connection.email && <span className="muted"> · {connection.email}</span>}
      </p>

      {connection.state !== 'connected' && (
        <a className="button" href={api.loginUrl}>
          {connection.state === 'needs_reconnect' ? 'Reconnect Google' : 'Connect Google'}
        </a>
      )}

      <p className="muted small">Environment: {me.env}</p>

      <button
        type="button"
        onClick={() => {
          void api.logout().then(onChange)
        }}
      >
        Sign out
      </button>
    </>
  )
}
