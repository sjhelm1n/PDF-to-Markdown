import {Document, Page, pdfjs} from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

export default function PdfPreview({source, page, width, onPages, onError}: {
  source: string
  page: number
  width: number
  onPages: (count: number) => void
  onError: (message: string) => void
}) {
  return (
    <Document file={source} onLoadSuccess={({numPages}) => onPages(numPages)}
      onLoadError={error => onError(error.message)} loading="Loading PDF…">
      <Page pageNumber={page} width={width} renderAnnotationLayer={false} />
    </Document>
  )
}
