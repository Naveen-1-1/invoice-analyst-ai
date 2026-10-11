import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { useListDeliveries, useListPurchaseOrders, useListVendors } from '@/api/generated'
import { WorkspacePicker, useWorkspace } from '@/components/WorkspacePicker'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { errorText, formatMoney, formatQty, formatShare } from '@/lib/format'

const share = formatShare
const qty = formatQty

function Vendors({ workspaceId, filter }: { workspaceId: number; filter: string }) {
  const vendors = useListVendors(workspaceId)
  if (vendors.isPending) return <Skeleton className="h-64 w-full" />
  if (vendors.isError) return <p className="text-sm text-red-700">{errorText(vendors.error)}</p>
  const rows = vendors.data.filter((v) => `${v.code} ${v.name}`.toLowerCase().includes(filter.toLowerCase()))
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Vendor</TableHead>
          <TableHead>Country</TableHead>
          <TableHead>Terms</TableHead>
          <TableHead>Account on file</TableHead>
          <TableHead className="text-right">Invoices</TableHead>
          <TableHead className="text-right">POs</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((v) => (
          <TableRow key={v.id}>
            <TableCell className="whitespace-normal">
              <Link to="/vendor/$vendorId" params={{ vendorId: String(v.id) }} className="font-medium hover:underline">{v.name}</Link>
              <div className="text-xs text-muted-foreground">
                {v.code}{v.origin === 'review' && ' · added in review'}{v.status !== 'active' && ` · ${v.status}`}
              </div>
            </TableCell>
            <TableCell>{v.country ?? '—'}</TableCell>
            <TableCell>{v.terms_days != null ? `${v.terms_days} days` : '—'}{v.discount && <div className="text-xs text-muted-foreground">{v.discount}</div>}</TableCell>
            <TableCell className="font-mono text-xs">{v.account ?? 'none'}</TableCell>
            <TableCell className="text-right tabular-nums">{v.invoices}</TableCell>
            <TableCell className="text-right tabular-nums">{v.purchase_orders}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function PurchaseOrders({ workspaceId, filter }: { workspaceId: number; filter: string }) {
  const pos = useListPurchaseOrders(workspaceId)
  if (pos.isPending) return <Skeleton className="h-64 w-full" />
  if (pos.isError) return <p className="text-sm text-red-700">{errorText(pos.error)}</p>
  const rows = pos.data.filter((p) => `${p.po_number} ${p.vendor ?? ''}`.toLowerCase().includes(filter.toLowerCase()))
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>PO</TableHead>
          <TableHead>Vendor · for</TableHead>
          <TableHead>Ordered on</TableHead>
          <TableHead className="text-right">Ordered</TableHead>
          <TableHead className="text-right" title="Share of the ordered quantity delivered">Received</TableHead>
          <TableHead className="text-right" title="Share of the ordered quantity on invoices (rejected ones excluded)">Invoiced</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((p) => (
          <TableRow key={p.id}>
            <TableCell><Link to="/purchase-order/$poId" params={{ poId: String(p.id) }} className="font-medium hover:underline">{p.po_number}</Link></TableCell>
            <TableCell className="whitespace-normal">{p.vendor ?? '—'}<div className="text-xs text-muted-foreground">{p.entity}</div></TableCell>
            <TableCell className="tabular-nums">{p.order_date ?? '—'}</TableCell>
            <TableCell className="text-right tabular-nums">{formatMoney(p.ordered, p.currency)}</TableCell>
            <TableCell className="text-right tabular-nums">{share(p.received_share)}</TableCell>
            <TableCell className="text-right tabular-nums">{share(p.invoiced_share)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Deliveries({ workspaceId, filter }: { workspaceId: number; filter: string }) {
  const grns = useListDeliveries(workspaceId)
  if (grns.isPending) return <Skeleton className="h-64 w-full" />
  if (grns.isError) return <p className="text-sm text-red-700">{errorText(grns.error)}</p>
  const rows = grns.data.filter((g) => `${g.grn_number} ${g.po_number} ${g.vendor ?? ''}`.toLowerCase().includes(filter.toLowerCase()))
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Delivery</TableHead>
          <TableHead>Received on</TableHead>
          <TableHead>For PO</TableHead>
          <TableHead>Vendor</TableHead>
          <TableHead className="text-right">Lines</TableHead>
          <TableHead className="text-right">Quantity</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((g) => (
          <TableRow key={g.id}>
            <TableCell className="font-medium">{g.grn_number}</TableCell>
            <TableCell className="tabular-nums">{g.received_date}</TableCell>
            <TableCell><Link to="/purchase-order/$poId" params={{ poId: String(g.po_id) }} className="hover:underline">{g.po_number}</Link></TableCell>
            <TableCell className="whitespace-normal">{g.vendor ?? '—'}</TableCell>
            <TableCell className="text-right tabular-nums">{g.lines}</TableCell>
            <TableCell className="text-right tabular-nums">{qty(g.quantity)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function VendorsPage() {
  const workspaceId = useWorkspace()
  const [filter, setFilter] = useState('')
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Vendors, orders and deliveries</h1>
          <p className="text-sm text-muted-foreground">
            What the checks compare invoices against: the vendor list (with bank accounts on file), purchase orders
            and goods received. View-only: vendors change only through review (a confirmed bank change, or an
            approved invoice from a new vendor).
          </p>
        </div>
        <WorkspacePicker section="/vendors" value={workspaceId} />
      </div>
      {workspaceId != null && (
        <Card>
          <CardContent className="pt-6">
            <Tabs defaultValue="vendors">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <TabsList>
                  <TabsTrigger value="vendors">Vendors</TabsTrigger>
                  <TabsTrigger value="pos">Purchase orders</TabsTrigger>
                  <TabsTrigger value="grns">Deliveries</TabsTrigger>
                </TabsList>
                <input type="search" aria-label="Filter" placeholder="Filter by name or number" value={filter}
                       onChange={(e) => setFilter(e.target.value)}
                       className="h-8 w-64 rounded-md border bg-background px-2 text-sm" />
              </div>
              <TabsContent value="vendors" className="pt-3"><Vendors workspaceId={workspaceId} filter={filter} /></TabsContent>
              <TabsContent value="pos" className="pt-3"><PurchaseOrders workspaceId={workspaceId} filter={filter} /></TabsContent>
              <TabsContent value="grns" className="pt-3"><Deliveries workspaceId={workspaceId} filter={filter} /></TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
