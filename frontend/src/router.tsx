import { Link, Outlet, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { useListReviewQueue } from '@/api/generated'
import { AuditPage } from '@/pages/AuditPage'
import { BoardPage } from '@/pages/BoardPage'
import { DocumentPage } from '@/pages/DocumentPage'
import { EvaluationPage } from '@/pages/EvaluationPage'
import { LedgerPage } from '@/pages/LedgerPage'
import { PaymentsPage } from '@/pages/PaymentsPage'
import { ReviewPage } from '@/pages/ReviewPage'
import { ReviewQueuePage } from '@/pages/ReviewQueuePage'
import { SettingsPage } from '@/pages/SettingsPage'
import { PurchaseOrderPage, VendorPage } from '@/pages/VendorPage'
import { VendorsPage } from '@/pages/VendorsPage'
import { RunPage } from '@/pages/RunPage'
import { RunsPage } from '@/pages/RunsPage'

function ReviewLink() {
  const queue = useListReviewQueue(undefined, { query: { refetchInterval: 30_000 } })
  const n = queue.data?.length ?? 0
  return (
    <Link to="/review" className="hover:text-foreground [&.active]:text-foreground">
      Review{n > 0 && <span className="ml-1 rounded-full bg-amber-100 px-1.5 text-xs text-amber-900 tabular-nums dark:bg-amber-950 dark:text-amber-200">{n}</span>}
    </Link>
  )
}

function Shell() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b">
        <div className="mx-auto flex max-w-[1600px] items-center gap-6 px-4 py-3">
          <Link to="/" className="font-semibold">invoice-analyst-ai</Link>
          <nav className="flex gap-4 text-sm text-muted-foreground">
            <Link to="/board" className="hover:text-foreground [&.active]:text-foreground">Board</Link>
            <ReviewLink />
            <Link to="/payments" className="hover:text-foreground [&.active]:text-foreground">Payments</Link>
            <Link to="/vendors" className="hover:text-foreground [&.active]:text-foreground">Vendors</Link>
            <Link to="/ledger" className="hover:text-foreground [&.active]:text-foreground">Ledger</Link>
            <Link to="/audit" className="hover:text-foreground [&.active]:text-foreground">Audit</Link>
            <Link to="/evaluation" className="hover:text-foreground [&.active]:text-foreground">Evaluation</Link>
            <Link to="/settings" className="hover:text-foreground [&.active]:text-foreground">Settings</Link>
            <Link to="/" className="hover:text-foreground [&.active]:text-foreground" activeOptions={{ exact: true }}>Runs</Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-[1600px] px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}

const rootRoute = createRootRoute({ component: Shell })
const runsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: RunsPage })
const runRoute = createRoute({ getParentRoute: () => rootRoute, path: '/runs/$runId', component: RunPage })
const boardRoute = createRoute({ getParentRoute: () => rootRoute, path: '/board', component: BoardPage })
const workspaceBoardRoute = createRoute({ getParentRoute: () => rootRoute, path: '/board/$workspaceId', component: BoardPage })
const reviewQueueRoute = createRoute({ getParentRoute: () => rootRoute, path: '/review', component: ReviewQueuePage })
const reviewRoute = createRoute({ getParentRoute: () => rootRoute, path: '/review/$invoiceId', component: ReviewPage })
const paymentsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/payments', component: PaymentsPage })
const workspacePaymentsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/payments/$workspaceId', component: PaymentsPage })
const ledgerRoute = createRoute({ getParentRoute: () => rootRoute, path: '/ledger', component: LedgerPage })
const workspaceLedgerRoute = createRoute({ getParentRoute: () => rootRoute, path: '/ledger/$workspaceId', component: LedgerPage })
const auditRoute = createRoute({ getParentRoute: () => rootRoute, path: '/audit', component: AuditPage })
const workspaceAuditRoute = createRoute({ getParentRoute: () => rootRoute, path: '/audit/$workspaceId', component: AuditPage })
const vendorsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/vendors', component: VendorsPage })
const workspaceVendorsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/vendors/$workspaceId', component: VendorsPage })
const vendorRoute = createRoute({ getParentRoute: () => rootRoute, path: '/vendor/$vendorId', component: VendorPage })
const poRoute = createRoute({ getParentRoute: () => rootRoute, path: '/purchase-order/$poId', component: PurchaseOrderPage })
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: SettingsPage })
const evaluationRoute = createRoute({ getParentRoute: () => rootRoute, path: '/evaluation', component: EvaluationPage })
const documentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/documents/$documentId',
  component: DocumentPage,
})

export const router = createRouter({ routeTree: rootRoute.addChildren([runsRoute, runRoute, boardRoute, workspaceBoardRoute, reviewQueueRoute, reviewRoute,
  paymentsRoute, workspacePaymentsRoute, ledgerRoute, workspaceLedgerRoute, auditRoute, workspaceAuditRoute, vendorsRoute,
  workspaceVendorsRoute, vendorRoute, poRoute, evaluationRoute, settingsRoute, documentRoute]) })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
