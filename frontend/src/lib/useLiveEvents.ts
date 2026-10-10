import { useEffect, useRef, useState } from 'react'

// Listen to the backend's audit events (server-sent events) and call onChange, at most every `throttleMs`.
// Returns whether the stream is connected, so callers can fall back to polling when it isn't.
export function useLiveEvents(workspaceId: number | undefined, onChange: () => void, throttleMs = 750): boolean {
  const [connected, setConnected] = useState(false)
  const callback = useRef(onChange)
  callback.current = onChange

  useEffect(() => {
    if (workspaceId == null) return
    const source = new EventSource(`/api/events?workspace_id=${workspaceId}`)
    let timer: ReturnType<typeof setTimeout> | null = null
    const fire = () => {
      if (timer) return
      timer = setTimeout(() => {
        timer = null
        callback.current()
      }, throttleMs)
    }
    source.addEventListener('ready', () => setConnected(true))
    source.addEventListener('audit', fire)
    source.onerror = () => setConnected(false) // the browser reconnects by itself
    return () => {
      if (timer) clearTimeout(timer)
      source.close()
      setConnected(false)
    }
  }, [workspaceId, throttleMs])

  return connected
}
