import {useRef, useState} from 'react'
import type {CSSProperties, ReactNode} from 'react'
import {readSession, writeSession} from './session'

const splitKey = 'pdf2md.pane-split'
const minSplit = 30
const maxSplit = 70

function clampSplit(width: number) {
  return Math.round(Math.min(maxSplit, Math.max(minSplit, width)))
}

export function ReviewPanes({pdf, markdown}: {pdf: ReactNode; markdown: ReactNode}) {
  const [split, setSplit] = useState(() => clampSplit(Number(readSession(splitKey)) || 50))
  const grid = useRef<HTMLElement>(null)

  function resize(proposed: number) {
    const width = clampSplit(proposed)
    setSplit(width)
    writeSession(splitKey, String(width))
  }

  const style = {'--pdf-pane': `${split}fr`, '--markdown-pane': `${100 - split}fr`} as CSSProperties
  return (
    <section className="panes" ref={grid} style={style}>
      {pdf}
      <div className="pane-divider" role="separator" aria-label="Resize review panes" aria-orientation="vertical"
        aria-valuemin={minSplit} aria-valuemax={maxSplit} aria-valuenow={split} tabIndex={0}
        onPointerDown={event => {
          if (event.button === 0) event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerMove={event => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId) || !grid.current) return
          const bounds = grid.current.getBoundingClientRect()
          resize((event.clientX - bounds.left) * 100 / bounds.width)
        }}
        onKeyDown={event => {
          const widths: Record<string, number> = {
            ArrowLeft: split - 5, ArrowRight: split + 5, Home: minSplit, End: maxSplit,
          }
          const width = widths[event.key]
          if (width === undefined) return
          event.preventDefault()
          resize(width)
        }}
      />
      {markdown}
    </section>
  )
}
