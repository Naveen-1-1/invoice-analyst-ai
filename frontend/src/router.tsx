import { Link, Outlet, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { useListReviewQueue } from '@/api/generated'
import { BoardPage } from '@/pages/BoardPage'
import { DocumentPage } from '@/pages/DocumentPage'
import { ReviewPage } from '@/pages/ReviewPage'
import { ReviewQueuePage } from '@/pages/ReviewQueuePage'
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
const documentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/documents/$documentId',
  component: DocumentPage,
})

export const router = createRouter({ routeTree: rootRoute.addChildren([runsRoute, runRoute, boardRoute, workspaceBoardRoute, reviewQueueRoute, reviewRoute, documentRoute]) })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
