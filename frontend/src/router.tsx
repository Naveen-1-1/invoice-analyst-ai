import { Link, Outlet, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { BoardPage } from '@/pages/BoardPage'
import { DocumentPage } from '@/pages/DocumentPage'
import { RunPage } from '@/pages/RunPage'
import { RunsPage } from '@/pages/RunsPage'

function Shell() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b">
        <div className="mx-auto flex max-w-[1600px] items-center gap-6 px-4 py-3">
          <Link to="/" className="font-semibold">invoice-analyst-ai</Link>
          <nav className="flex gap-4 text-sm text-muted-foreground">
            <Link to="/board" className="hover:text-foreground [&.active]:text-foreground">Board</Link>
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
const documentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/documents/$documentId',
  component: DocumentPage,
})

export const router = createRouter({ routeTree: rootRoute.addChildren([runsRoute, runRoute, boardRoute, workspaceBoardRoute, documentRoute]) })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
