import React, {lazy, Suspense, useEffect, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {UploadCloud, FileText, Download, RotateCcw, Save, Link2, Image as ImageIcon} from 'lucide-react'
import {jobSubmissionError, useEngines} from './engines'
import {createMarkdownDraft, updateMarkdownDraft} from './markdownDraft'
import {ReviewPanes} from './ReviewPanes'
import {readSession, writeSession} from './session'
import './style.css'
const PdfPreview = lazy(() => import('./PdfPreview'))
const MarkdownPreview = lazy(() => import('./MarkdownPreview'))

type Job = {id:string; filename:string; engine:string; state:string; error?:string|null; markdown?:string; pages?:string[]; page_anchors?:boolean; images?:{path:string; source_page:number|null}[]}
const activeJobKey = 'pdf2md.active-job'

function readActiveJobId() {
  const id = readSession(activeJobKey)
  return id && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(id) ? id : null
}

function App(){
  const [file,setFile]=useState<File|null>(null)
  const [drag,setDrag]=useState(false)
  const [jobId,setJobId]=useState(readActiveJobId)
  const restoredJob = useRef(jobId)
  const [job,setJob]=useState<Job|null>(null)
  const [draft, setDraft] = useState(createMarkdownDraft)
  const [mode,setMode]=useState<'preview'|'edit'>('preview')
  const [page,setPage]=useState(1)
  const [numPages,setNumPages]=useState(0)
  const [saving,setSaving]=useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error,setError]=useState('')
  const [sync,setSync]=useState(true)
  const [viewWidth,setViewWidth]=useState(520)
  const pdfContainer=useRef<HTMLDivElement>(null)
  const markdownContainer = useRef<HTMLDivElement>(null)
  const syncedScroll = useRef<{pane: HTMLDivElement; top: number} | null>(null)
  const objectUrl=useRef<string|null>(null)
  const submission = useRef<AbortController | null>(null)
  const {engines, selectedEngine, setSelectedId, status: engineStatus} = useEngines()
  const busy = submitting || job?.state === 'queued' || job?.state === 'running'
  const jobEngineName = engines.find(engine => engine.id === job?.engine)?.name ?? job?.engine

  const {markdown, savedMarkdown} = draft
  const saved = markdown === savedMarkdown
  const draftAtRisk = !saved || saving
  const pdfSource = file ? objectUrl.current : job ? `/api/jobs/${job.id}/pdf` : null
  const filename = file?.name ?? job?.filename
  const pageDraft = draft.pages?.[page - 1]
  const editorPageAligned = sync && pageDraft !== undefined
  // Original excerpts can still be previewed when no exact editable page map exists.
  const hasEdits = markdown !== (job?.markdown ?? '')
  const originalExcerpt = hasEdits || (job && restoredJob.current === job.id)
    ? undefined : job?.pages?.[page - 1] || undefined
  const linkedMarkdown = sync ? pageDraft ?? originalExcerpt : undefined
  const pageAligned = linkedMarkdown !== undefined
  const previewHint = !sync ? '' : pageAligned
    ? 'Page-aligned Markdown preview. Scroll positions within the page are approximate.'
    : hasEdits
      ? 'Page linking is paused for edited Markdown. Showing the full document.'
      : 'This page has no linked Markdown excerpt. Showing the full document.'

  useEffect(() => () => {
    submission.current?.abort()
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
  }, [])

  useEffect(() => writeSession(activeJobKey, jobId), [jobId])

  useEffect(() => {
    if (!draftAtRisk) return
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault()
      event.returnValue = true
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [draftAtRisk])

  useEffect(() => {
    if (!jobId) return
    const controller = new AbortController()
    let timer: number | undefined

    async function poll() {
      try {
        const response = await fetch(`/api/jobs/${jobId}`, {signal: controller.signal})
        if (controller.signal.aborted) return
        if (response.status === 404 || response.status === 400) {
          setJob(null)
          setJobId(null)
          setError('The saved conversion is no longer available.')
          return
        }
        if (!response.ok) throw new Error(await response.text())
        const data: Job = await response.json()
        if (controller.signal.aborted) return
        setJob(data)
        if (data.state === 'complete') {
          setDraft(createMarkdownDraft(data.id, data.markdown ?? '', data.pages, data.page_anchors))
        } else if (data.state !== 'failed' && data.state !== 'interrupted') {
          timer = window.setTimeout(poll, 1500)
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e))
      }
    }

    void poll()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [jobId])

  useEffect(()=>{const node=pdfContainer.current;if(!node)return;const observer=new ResizeObserver(([entry])=>setViewWidth(Math.min(720,Math.max(200,entry.contentRect.width-30))));observer.observe(node);return()=>observer.disconnect()},[job?.state])

  function selectFile(next:File|undefined){if(!next)return;if(!next.name.toLowerCase().endsWith('.pdf')){setError('Please choose a PDF.');return}
    if(draftAtRisk&&!window.confirm('Leave this conversion before the Markdown is saved?'))return;
    submission.current?.abort()
    submission.current = null
    setSubmitting(false)
    if(objectUrl.current)URL.revokeObjectURL(objectUrl.current);
    objectUrl.current=URL.createObjectURL(next);setFile(next);setJobId(null);setJob(null);setDraft(createMarkdownDraft());setPage(1);setNumPages(0);setError('')
  }
  async function convert() {
    if (!pdfSource || !filename || !selectedEngine || busy || saving || submission.current) return
    if (!saved && !window.confirm('Discard unsaved edits and reconvert?')) return

    const controller = new AbortController()
    submission.current = controller
    setSubmitting(true)
    setError('')
    const query = new URLSearchParams({ engine: selectedEngine.id })

    try {
      const body = new FormData()
      if (file) {
        body.append('file', file)
      } else {
        const response = await fetch(pdfSource, {signal: controller.signal})
        if (!response.ok) throw new Error('Could not load the saved PDF.')
        body.append('file', await response.blob(), filename)
      }
      const response = await fetch(`/api/jobs?${query}`, { method: 'POST', body, signal: controller.signal })
      if (!response.ok) throw new Error(await jobSubmissionError(response))
      const result: { id: string } = await response.json()
      if (controller.signal.aborted) return
      setJob({ id: result.id, state: 'queued', filename, engine: selectedEngine.id })
      setJobId(result.id)
      setPage(1)
      setDraft(createMarkdownDraft(result.id))
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (submission.current === controller) {
        submission.current = null
        setSubmitting(false)
      }
    }
  }
  async function save() {
    if (!jobId) return
    setSaving(true)
    try {
      const response = await fetch(`/api/jobs/${jobId}/markdown`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({markdown}),
      })
      if (!response.ok) throw new Error(await response.text())
      setDraft(current => current.jobId === jobId ? {...current, savedMarkdown: markdown} : current)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  function scrollReview(source: HTMLDivElement, target: HTMLDivElement | null) {
    const mirrored = syncedScroll.current
    if (mirrored?.pane === source) {
      syncedScroll.current = null
      if (source.scrollTop === mirrored.top) return
    }
    if (!pageAligned || mode !== 'preview' || !target) return
    const sourceRange = source.scrollHeight - source.clientHeight
    const targetRange = target.scrollHeight - target.clientHeight
    if (sourceRange <= 0 || targetRange <= 0) return

    const top = Math.round(source.scrollTop * targetRange / sourceRange)
    if (Math.abs(target.scrollTop - top) < 1) return
    target.scrollTop = top
    syncedScroll.current = {pane: target, top: target.scrollTop}
  }

  return <div className="app">
    <header><div className="identity"><div className="brand"><FileText size={24}/></div><div><strong>PDF to Markdown</strong><small>Local document conversion workspace</small></div></div></header>
    <main>
      <section className="toolbar">
        <label className={'upload '+(drag?'drag':'')} onDragOver={e=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={e=>{e.preventDefault();setDrag(false);selectFile(e.dataTransfer.files[0])}}>
          <UploadCloud size={22}/><div><b>{filename ?? 'Drop a PDF here'}</b><small>{filename ? 'Click to replace document' : 'or click to browse'}</small></div>
          <input aria-label="Select PDF" type="file" accept=".pdf,application/pdf" onChange={e=>selectFile(e.target.files?.[0])}/>
        </label>
        <div className="field engine-field">
          <label htmlFor="conversion-engine">Conversion engine</label>
          <select id="conversion-engine" value={selectedEngine?.id ?? ''}
            disabled={busy || !selectedEngine} onChange={event => setSelectedId(event.target.value)}>
            {!selectedEngine && <option value="">{engineStatus === 'loading' ? 'Loading engines…' : 'No engines available'}</option>}
            {engines.map(engine => (
              <option key={engine.id} value={engine.id} disabled={!engine.available}>
                {engine.available ? engine.name : `${engine.name} (unavailable: ${engine.unavailable_reason ?? 'local runtime not ready'})`}
              </option>
            ))}
          </select>
        </div>
        <button className="primary" disabled={!pdfSource || !selectedEngine || busy || saving} onClick={convert}><RotateCcw size={16}/> Convert PDF</button>
      </section>
      <div className="status" role="status"><span>{submitting?'Submitting PDF…':job?.state==='complete'?'✓ Conversion complete':job?.state==='running'?`Converting with ${jobEngineName}…`:job?.state==='queued'?'Queued for processing':!job&&jobId?'Restoring conversion…':file&&selectedEngine?'Ready to convert':file?'Select an available engine':'Select a PDF to begin'}</span><span>{job?.images?.length!==undefined?`${job.images.length} PNG image(s) extracted`:''}</span></div>
      {engineStatus === 'error' && <div className="error" role="alert">Could not load conversion engines. Check the local API and reload the page.</div>}
      {engineStatus === 'ready' && !selectedEngine && <div className="error" role="alert">
        {engines.length === 0 ? 'No conversion engines are registered.' : engines.map(engine => (
          <div key={engine.id}>{engine.name} unavailable. {engine.unavailable_reason}</div>
        ))}
      </div>}
      {error&&<div className="error" role="alert">{error}</div>}{job?.error&&<div className="error" role="alert">{job.error}</div>}
      <section className="review-head"><h2>Document review</h2><div className="actions"><label className="check"><input type="checkbox" checked={sync} onChange={e=>setSync(e.target.checked)}/><Link2 size={14}/> Linked page navigation</label><div className="segmented"><button className={mode==='preview'?'on':''} onClick={()=>setMode('preview')}>Preview</button><button className={mode==='edit'?'on':''} onClick={()=>setMode('edit')}>Edit Markdown</button></div></div></section>
      <ReviewPanes pdf={
        <article className="pane">
          <div className="pane-title">Original PDF <span>{numPages ? `${numPages} pages` : ''}</span></div>
          <div className="pane-body pdf" ref={pdfContainer}
            onScroll={event => scrollReview(event.currentTarget, markdownContainer.current)}>
            {pdfSource ? (
              <Suspense fallback="Loading PDF viewer…">
                <PdfPreview source={pdfSource} page={page} width={viewWidth} onPages={setNumPages} onError={setError} />
              </Suspense>
            ) : (
              <div className="empty"><FileText size={38}/><p>Original PDF preview</p><small>Upload a PDF to begin</small></div>
            )}
          </div>
          <div className="pane-foot">
            <button disabled={page <= 1} onClick={() => setPage(current => current - 1)}>← Previous</button>
            <span>Page {page}{numPages ? ` of ${numPages}` : ''}</span>
            <button disabled={page >= numPages} onClick={() => setPage(current => current + 1)}>Next →</button>
          </div>
        </article>
      } markdown={
        <article className="pane">
          <div className="pane-title">Converted Markdown <span>{!saved ? 'Unsaved edits' : job?.state === 'complete' ? 'Ready for review' : ''}</span></div>
          <div className="pane-body markdown" ref={markdownContainer}
            onScroll={event => scrollReview(event.currentTarget, pdfContainer.current)}>
            {job?.state === 'complete' ? (
              mode === 'edit' ? (
                <div className="markdown-editor">
                  {sync && !editorPageAligned && <p className="hint">Page-aligned editing is unavailable for this draft. Editing the full document.</p>}
                  <textarea key={editorPageAligned ? page : 'full'} disabled={submitting} spellCheck={false}
                    aria-label={editorPageAligned ? `Markdown editor for page ${page}` : 'Markdown editor'}
                    value={editorPageAligned ? pageDraft : markdown}
                    onChange={event => {
                      const text = event.target.value
                      setDraft(current => updateMarkdownDraft(current, text, editorPageAligned ? page - 1 : undefined))
                    }} />
                </div>
              ) : (
                <Suspense fallback="Loading Markdown preview…">
                  <MarkdownPreview jobId={jobId} markdown={linkedMarkdown ?? markdown} hint={previewHint} />
                </Suspense>
              )
            ) : (
              <div className="empty"><FileText size={38}/><p>Markdown preview</p><small>Converted content will appear here</small></div>
            )}
          </div>
          <div className="pane-foot">
            <span>{mode === 'edit'
              ? editorPageAligned ? `Editing page ${page}` : 'Full document editor'
              : pageAligned ? `Page ${page} preview` : 'Full document preview'}</span>
            {((mode === 'edit' ? editorPageAligned : pageAligned) && numPages > 1) && (
              <select aria-label="Markdown source page" value={page} onChange={event => setPage(Number(event.target.value))}>
                {Array.from({length: numPages}, (_, index) => (
                  <option value={index + 1} key={index + 1}>Page {index + 1}</option>
                ))}
              </select>
            )}
            <span>{markdown.length.toLocaleString()} chars</span>
          </div>
        </article>
      } />
      <footer><div className="footnote"><ImageIcon size={16}/> PNG figure exports are included in the ZIP. No AI diagram interpretation.</div><div className="actions"><button disabled={!jobId||job?.state!=='complete'||saved||saving||submitting} onClick={save}><Save size={16}/>{saving?'Saving…':'Save edits'}</button><button className="primary" disabled={!jobId||job?.state!=='complete'||draftAtRisk||submitting} onClick={()=>window.location.assign(`/api/jobs/${jobId}/download`)}><Download size={16}/> Download ZIP</button></div></footer>
    </main>
  </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
