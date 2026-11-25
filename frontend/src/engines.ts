import { useEffect, useState } from 'react'

type EngineCapabilities = {
  markdown: boolean
  page_markdown: boolean
  png_images: boolean
  figure_provenance: boolean
}

export type Engine = {
  id: string
  name: string
  adapter_version: string
  engine_version: string | null
  enabled: boolean
  available: boolean
  unavailable_reason: string | null
  capabilities: EngineCapabilities
}

type EngineCatalog = { default_engine: string; engines: Engine[] }

type Discovery =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; catalog: EngineCatalog }

export function useEngines() {
  const [discovery, setDiscovery] = useState<Discovery>({ status: 'loading' })
  const [selectedId, setSelectedId] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    async function discover() {
      try {
        const response = await fetch('/api/engines', { signal: controller.signal })
        if (!response.ok) throw new Error('Engine discovery failed')
        const catalog: EngineCatalog = await response.json()
        if (!Array.isArray(catalog?.engines) || typeof catalog.default_engine !== 'string') {
          throw new Error('Invalid engine catalog')
        }
        if (!controller.signal.aborted) setDiscovery({ status: 'ready', catalog })
      } catch {
        if (!controller.signal.aborted) setDiscovery({ status: 'error' })
      }
    }

    void discover()
    return () => controller.abort()
  }, [])

  const engines = discovery.status === 'ready' ? discovery.catalog.engines : []
  const defaultId = discovery.status === 'ready' ? discovery.catalog.default_engine : undefined
  const selectedEngine =
    engines.find(engine => engine.available && engine.id === selectedId) ??
    engines.find(engine => engine.available && engine.id === defaultId) ??
    engines.find(engine => engine.available)

  return { engines, selectedEngine, setSelectedId, status: discovery.status }
}

export async function jobSubmissionError(response: Response): Promise<string> {
  const fallback = `Could not start conversion (HTTP ${response.status}).`
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') return body.detail
    if (typeof body?.detail?.message === 'string') return body.detail.message
  } catch {
    // A reverse proxy can return a non-JSON error page.
  }
  return fallback
}
