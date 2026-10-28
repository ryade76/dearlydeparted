import type { Citation } from '../api/types'

interface DocumentViewerModalProps {
  citation: Citation | null
  onClose: () => void
}

export function DocumentViewerModal({ citation, onClose }: DocumentViewerModalProps) {
  if (!citation) return null

  const isTextOnly = !citation.file_url && !!citation.text_excerpt
  if (!citation.file_url && !isTextOnly) return null

  const isImage = citation.doc_type === 'image'
  const isPdf = citation.path?.endsWith('.pdf')
  const isText = /\.(txt|json|xml|md|html?)$/i.test(citation.path || '')
  const fileName = citation.path?.split('/').pop() || 'document'

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b shrink-0">
          <div className="min-w-0 mr-4">
            <h3 className="font-semibold truncate text-sm">{fileName}</h3>
            <p className="text-xs text-slate-500 truncate">
              {[citation.domain, citation.bucket].filter(Boolean).join(' / ')}
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            {citation.file_url && (
              <a
                href={citation.file_url}
                download={fileName}
                className="px-3 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
              >
                Download
              </a>
            )}
            <button
              onClick={onClose}
              className="px-2 py-1 text-slate-500 hover:text-slate-800 text-lg"
            >
              &times;
            </button>
          </div>
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-auto p-4">
          {isTextOnly && citation.text_excerpt && (
            <div className="font-mono text-sm whitespace-pre-wrap bg-slate-50 p-4 rounded border">
              {citation.text_excerpt}
            </div>
          )}
          {isImage && citation.file_url && (
            <img
              src={citation.file_url}
              alt={fileName}
              className="max-w-full h-auto mx-auto"
            />
          )}
          {isPdf && citation.file_url && (
            <iframe src={citation.file_url} className="w-full h-[70vh]" title={fileName} />
          )}
          {isText && citation.file_url && (
            <iframe
              src={citation.file_url}
              className="w-full h-[70vh] font-mono"
              title={fileName}
            />
          )}
          {!isImage && !isPdf && !isText && !isTextOnly && citation.file_url && (
            <div className="text-center py-12">
              <p className="text-slate-500 mb-4">Preview not available for this file type.</p>
              <a
                href={citation.file_url}
                download={fileName}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                Download {fileName}
              </a>
            </div>
          )}
        </div>

        {/* Footer: text excerpt for non-text-only citations */}
        {!isTextOnly && citation.text_excerpt && (
          <div className="p-4 border-t bg-slate-50 text-sm text-slate-600 max-h-32 overflow-auto shrink-0">
            <p className="font-semibold text-xs text-slate-400 mb-1">Cited excerpt:</p>
            {citation.text_excerpt}
          </div>
        )}
      </div>
    </div>
  )
}
