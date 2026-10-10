import { useNavigate, useParams } from '@tanstack/react-router'
import { useListWorkspaces } from '@/api/generated'

type Section = '/payments' | '/ledger' | '/audit'

// The workspace a page shows: from the address (/payments/12), else the first one; and a picker to switch.
export function useWorkspace(): number | undefined {
  const params = useParams({ strict: false }) as { workspaceId?: string }
  const workspaces = useListWorkspaces()
  return params.workspaceId ? Number(params.workspaceId) : workspaces.data?.find((w) => w.kind !== 'benchmark')?.id
}

export function WorkspacePicker({ section, value }: { section: Section; value: number | undefined }) {
  const navigate = useNavigate()
  const workspaces = useListWorkspaces()
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">Workspace</span>
      <select
        className="max-w-80 rounded-md border bg-background px-2 py-1 text-sm"
        value={value ?? ''}
        onChange={(e) => navigate({ to: `${section}/$workspaceId`, params: { workspaceId: e.target.value } })}
      >
        {(workspaces.data ?? []).filter((w) => w.kind !== 'benchmark').map((w) => (
          <option key={w.id} value={w.id}>{w.name}</option>
        ))}
      </select>
    </label>
  )
}
