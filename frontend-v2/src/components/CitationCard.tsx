import { cn } from '../lib/cn'
import type { Citation } from '../api/types'

interface CitationCardProps {
  citation: Citation
  onClick?: (citation: Citation) => void
}

export function CitationCard({ citation, onClick }: CitationCardProps) {
  return (
    <div
      className={cn(
        'rounded border px-3 py-2 text-xs',
        citation.verified ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50',
        onClick && 'cursor-pointer hover:shadow-md transition-shadow',
      )}
      onClick={() => onClick?.(citation)}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-semibold text-slate-800">[{citation.index + 1}]</span>
        <div className="flex items-center gap-1">
          {citation.doc_type && (
            <span className="px-1 py-0.5 bg-blue-50 text-blue-700 rounded text-[10px]">
              {citation.doc_type}
            </span>
          )}
          <span
            className={cn(
              'px-1.5 py-0.5 rounded text-[10px] font-medium',
              citation.verified ? 'bg-emerald-200 text-emerald-800' : 'bg-red-200 text-red-800',
            )}
          >
            {citation.verified ? 'Verified' : 'Unverified'}
            {citation.verification_score > 0 && ` (${(citation.verification_score * 100).toFixed(0)}%)`}
          </span>
        </div>
      </div>
      <div className="text-slate-600 truncate font-mono" title={citation.path}>
        {citation.file_url ? (
          <a
            href={citation.file_url}
            className="text-blue-600 hover:underline"
            onClick={(e) => e.stopPropagation()}
            target="_blank"
            rel="noopener noreferrer"
          >
            {citation.path}
          </a>
        ) : (
          citation.path
        )}
      </div>
      {citation.text_excerpt && (
        <p className="mt-1 text-slate-500 line-clamp-2">{citation.text_excerpt}</p>
      )}
      {citation.doc_type === 'image' && citation.file_url && (
        <img
          src={citation.file_url}
          alt=""
          className="mt-2 max-h-24 rounded border object-cover"
          loading="lazy"
        />
      )}
      <div className="mt-1.5 flex gap-2 flex-wrap">
        {citation.domain && (
          <span className="px-1 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px]">{citation.domain}</span>
        )}
        {citation.bucket && (
          <span className="px-1 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px]">{citation.bucket}</span>
        )}
        {citation.date && (
          <span className="px-1 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px]">{citation.date}</span>
        )}
        <span className="px-1 py-0.5 bg-slate-100 text-slate-500 rounded text-[10px]">
          {citation.verification_method}
        </span>
      </div>
    </div>
  )
}
