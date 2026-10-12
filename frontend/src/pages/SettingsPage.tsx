import { Link } from '@tanstack/react-router'
import { useGetSettings } from '@/api/generated'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { errorText } from '@/lib/format'

// Decision 49: what the system uses now. View-only.
export function SettingsPage() {
  const settings = useGetSettings()
  if (settings.isPending) return <Skeleton className="h-96 w-full" />
  if (settings.isError) return <p className="text-sm text-red-700">Couldn't load the settings: {errorText(settings.error)}</p>
  const s = settings.data
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Every threshold, cap, tolerance, rate and model as the system uses it now, read from the code that uses it.
          View-only, so the system stays as it was evaluated; each workspace's current date is set on the Payments
          page.
        </p>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {s.sections.map((sec) => (
          <Card key={sec.title}>
            <CardHeader><CardTitle className="text-base">{sec.title}</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <TableBody>
                  {sec.items.map((i) => (
                    <TableRow key={i.name}>
                      <TableCell className="w-48 align-top text-muted-foreground whitespace-normal">{i.name}</TableCell>
                      <TableCell className="align-top whitespace-normal">
                        <div className="font-medium break-words">{i.value}</div>
                        {i.explanation && <div className="text-xs text-muted-foreground">{i.explanation}</div>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
        <Card>
          <CardHeader><CardTitle className="text-base">Today, per workspace</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Workspace</TableHead><TableHead>Today</TableHead><TableHead /></TableRow></TableHeader>
              <TableBody>
                {s.workspaces.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell className="whitespace-normal">{w.name}</TableCell>
                    <TableCell className="tabular-nums">{w.today}{w.live && <span className="text-muted-foreground"> (simulated clock)</span>}</TableCell>
                    <TableCell className="text-right"><Link to="/payments/$workspaceId" params={{ workspaceId: String(w.id) }} className="text-xs hover:underline">Payments</Link></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
