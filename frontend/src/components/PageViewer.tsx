import type { FieldOut, PageOut, WordOut } from '@/api/generated'
import { TIER_STROKE } from '@/components/TierBadge'
import { FIELD_LABELS } from '@/lib/format'

type Props = {
  pages: PageOut[]
  fields: FieldOut[]
  hoveredId: number | null
  onHover: (id: number | null) => void
  onSelect: (id: number) => void
  // review: while correcting a field, every word on the page can be clicked to fill it (shift-click adds words)
  words?: Record<number, WordOut[]>
  picked?: { page_no: number; bbox: number[] } | null
  onPickWord?: (page: PageOut, word: WordOut, extend: boolean) => void
}

// Page images with a box over every located field. Boxes are stored normalised (0-1) to the page image, so an
// SVG with viewBox 0 0 1 1 stretched over the image lines them up at any size.
export function PageViewer({ pages, fields, hoveredId, onHover, onSelect, words, picked, onPickWord }: Props) {
  const picking = Boolean(onPickWord && words)
  return (
    <div className="flex flex-col gap-4">
      {pages.map((page) => {
        const boxes = fields.filter((f) => f.page_no === page.page_no && f.bbox && f.bbox.length === 4)
        return (
          <figure
            key={page.id}
            className="relative overflow-hidden rounded-md border bg-white shadow-sm"
            style={{ aspectRatio: `${page.width} / ${page.height}` }} // keeps the page's space while it loads
          >
            <img
              src={page.image_url}
              alt={`Page ${page.page_no}`}
              className="absolute inset-0 h-full w-full select-none"
              draggable={false}
            />
            <svg
              className={`absolute inset-0 h-full w-full ${picking ? 'pointer-events-none opacity-40' : ''}`}
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
              role="group"
              aria-label={`Field locations on page ${page.page_no}`}
            >
              {boxes.map((f) => {
                const [x0, y0, x1, y1] = f.bbox as number[]
                const padX = 0.003
                const padY = 0.0015 // tight vertically, so a box never seems to frame the line above
                const active = f.id === hoveredId
                const stroke = TIER_STROKE[f.tier ?? ''] ?? '#6b7280'
                return (
                  <rect
                    key={f.id}
                    id={`box-${f.id}`}
                    x={x0 - padX}
                    y={y0 - padY}
                    width={x1 - x0 + 2 * padX}
                    height={y1 - y0 + 2 * padY}
                    fill={active ? stroke : 'transparent'}
                    fillOpacity={active ? 0.18 : 0}
                    stroke={stroke}
                    strokeOpacity={active ? 1 : 0.55}
                    strokeWidth={active ? 2.5 : 1.25}
                    vectorEffect="non-scaling-stroke"
                    className="cursor-pointer transition-[stroke-width,fill-opacity]"
                    onMouseEnter={() => onHover(f.id)}
                    onMouseLeave={() => onHover(null)}
                    onClick={() => onSelect(f.id)}
                  >
                    <title>{`${FIELD_LABELS[f.field] ?? f.field}${f.line_no ? ` (line ${f.line_no})` : ''}: ${f.evidence_text ?? ''}`}</title>
                  </rect>
                )
              })}
            </svg>
            {picking && (
              <svg
                className="absolute inset-0 h-full w-full cursor-crosshair"
                viewBox="0 0 1 1"
                preserveAspectRatio="none"
                role="group"
                aria-label={`Words on page ${page.page_no}: click one to use it`}
              >
                {(words?.[page.page_no] ?? []).map((w, i) => {
                  const [x0, y0, x1, y1] = w.bbox
                  return (
                    <rect
                      key={i}
                      x={x0 - 0.002}
                      y={y0 - 0.001}
                      width={x1 - x0 + 0.004}
                      height={y1 - y0 + 0.002}
                      className="fill-sky-500/0 stroke-sky-600/40 transition-colors hover:fill-sky-500/25 hover:stroke-sky-700"
                      strokeWidth={1}
                      vectorEffect="non-scaling-stroke"
                      onClick={(e) => onPickWord?.(page, w, e.shiftKey)}
                    >
                      <title>{w.t}</title>
                    </rect>
                  )
                })}
                {picked && picked.page_no === page.page_no && (
                  <rect
                    x={picked.bbox[0] - 0.003}
                    y={picked.bbox[1] - 0.0015}
                    width={picked.bbox[2] - picked.bbox[0] + 0.006}
                    height={picked.bbox[3] - picked.bbox[1] + 0.003}
                    className="pointer-events-none fill-sky-500/20 stroke-sky-700"
                    strokeWidth={2}
                    vectorEffect="non-scaling-stroke"
                  />
                )}
              </svg>
            )}
            <figcaption className="absolute right-2 bottom-2 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white">
              Page {page.page_no} of {pages.length}
            </figcaption>
          </figure>
        )
      })}
    </div>
  )
}
