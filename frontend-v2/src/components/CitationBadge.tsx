import { cn } from '../lib/cn'

interface CitationBadgeProps {
  index: number
  verified: boolean
  onClick?: () => void
}

export function CitationBadge({ index, verified, onClick }: CitationBadgeProps) {
  return (
    <span
      onClick={onClick}
      className={cn(
        'inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 rounded text-[10px] font-bold cursor-default',
        verified
          ? 'bg-emerald-100 text-emerald-700 border border-emerald-300'
          : 'bg-red-100 text-red-700 border border-red-300',
        onClick && 'cursor-pointer hover:opacity-80',
      )}
    >
      {index}
    </span>
  )
}
