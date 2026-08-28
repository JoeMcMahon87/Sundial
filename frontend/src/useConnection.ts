import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, type Me } from './api'

type State =
  | { status: 'loading' }
  | { status: 'signed-out' }
  | { status: 'ready'; me: Me }
  | { status: 'error'; message: string }

export function useConnection() {
  const [state, setState] = useState<State>({ status: 'loading' })

  const refresh = useCallback(async () => {
    try {
      setState({ status: 'ready', me: await api.me() })
    } catch (error) {
      if (error instanceof ApiError && error.problem.status === 401) {
        setState({ status: 'signed-out' })
        return
      }
      setState({
        status: 'error',
        message: error instanceof Error ? error.message : 'Unknown error',
      })
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return { state, refresh }
}
