import { Link } from '@tanstack/react-router'
import { useListRuns } from '@/api/generated'
import { TierBadge } from '@/components/TierBadge'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText, formatDateTime } from '@/lib/format'

export function RunsPage() {
  const runs = useListRuns()
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Runs</h1>
        <p className="text-sm text-muted-foreground">
          Each benchmark run extracts every document on its own. Open one to see its documents.
        </p>
      </div>
      {runs.isPending && <Skeleton className="h-40 w-full" />}
      {runs.isError && <p className="text-sm text-red-700">Couldn't load runs: {errorText(runs.error)}</p>}
      {runs.data && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-16">Run</TableHead>
              <TableHead>Label</TableHead>
              <TableHead>Dataset</TableHead>
              <TableHead className="text-right">Documents</TableHead>
              <TableHead>Invoice confidence</TableHead>
              <TableHead>Started</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.data.map((r) => (
              <TableRow key={r.id}>
                <TableCell>
                  <Link to="/runs/$runId" params={{ runId: String(r.id) }} className="font-medium underline-offset-4 hover:underline">
                    {r.id}
                  </Link>
                </TableCell>
                <TableCell>{r.label ?? '—'}</TableCell>
                <TableCell>{r.dataset}</TableCell>
                <TableCell className="text-right tabular-nums">{r.documents}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {(['high', 'medium', 'low'] as const)
                      .filter((t) => r.tiers[t])
                      .map((t) => (
                        <span key={t} className="flex items-center gap-1 text-xs">
                          <TierBadge tier={t} /> {r.tiers[t]}
                        </span>
                      ))}
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">{formatDateTime(r.started_at)}</TableCell>
                <TableCell>{r.status}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
