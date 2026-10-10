import type { FieldOut, PageOut } from '@/api/generated'
import { TIER_STROKE } from '@/components/TierBadge'
import { FIELD_LABELS } from '@/lib/format'

type Props = {
  pages: PageOut[]
  fields: FieldOut[]
  hoveredId: number | null
  onHover: (id: number | null) => void
  onSelect: (id: number) => void
}

// Page images with a box over every located field. Boxes are stored normalised (0-1) to the page image, so an
// SVG with viewBox 0 0 1 1 stretched over the image lines them up at any size.
export function PageViewer({ pages, fields, hoveredId, onHover, onSelect }: Props) {
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
              className="absolute inset-0 h-full w-full"
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
            <figcaption className="absolute right-2 bottom-2 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white">
              Page {page.page_no} of {pages.length}
            </figcaption>
          </figure>
        )
      })}
    </div>
  )
}
