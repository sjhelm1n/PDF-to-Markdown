import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {hidePageAnchors} from './markdownDraft'

function renderImage(jobId: string | null, src: string, alt: string) {
  if (!jobId) return <span>{alt}</span>
  if (/^(https?:|data:|\/\/)/i.test(src)) return <span>[External image blocked: {alt}]</span>

  const invalidImage = <span>[Image not in export: {alt}]</span>
  const path = src.replace(/^\.\//, '')
  if (!path.startsWith('images/')) return invalidImage

  let segments: string[]
  try {
    segments = path.slice('images/'.length).split('/').map(segment => decodeURIComponent(segment))
  } catch {
    return invalidImage
  }
  if (segments.some(segment => !segment || segment === '.' || segment.includes('..')
    || segment.includes('/') || segment.includes('\\') || segment.includes('\0'))) return invalidImage

  const encodedPath = segments.map(segment => encodeURIComponent(segment)).join('/')
  return <img loading="lazy" alt={alt} src={`/api/jobs/${jobId}/assets/${encodedPath}`} />
}

export default function MarkdownPreview({markdown, hint, jobId}: {markdown: string; hint: string; jobId: string | null}) {
  return (
    <div className="md-content">
      {hint && <p className="hint">{hint}</p>}
      <ReactMarkdown remarkPlugins={[remarkGfm, hidePageAnchors]}
        components={{img: ({src, alt}) => renderImage(jobId, src || '', alt || '')}}>
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
