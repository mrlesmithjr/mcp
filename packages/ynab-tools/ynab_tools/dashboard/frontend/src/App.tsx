import { createContext, useContext, useState, useEffect } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { LayoutDashboard, TrendingUp, Target, Coins, BarChart2, CalendarDays, RefreshCw, ClipboardList, Settings, Landmark, CreditCard, Gauge, Wallet, DollarSign, Activity, GitBranch, Inbox, CheckSquare, PieChart } from "lucide-react"
import { Toaster, toast } from "sonner"
import { cn } from "@/lib/utils"
import { Overview } from "@/pages/Overview"
import { TargetCalibration } from "@/pages/TargetCalibration"
import { SinkingFunds } from "@/pages/SinkingFunds"
import { CustomReport } from "@/pages/CustomReport"
import { Upcoming } from "@/pages/Upcoming"
import { Audit } from "@/pages/Audit"
import { Churn } from "@/pages/Churn"
import { TwoPot } from "@/pages/TwoPot"
import { Admin } from "@/pages/Admin"
import { Retirement } from "@/pages/Retirement"
import { NetWorth } from "@/pages/NetWorth"
import { Subscriptions } from "@/pages/Subscriptions"
import { SpendingPace } from "@/pages/SpendingPace"
import { PaycheckFunding } from "@/pages/PaycheckFunding"
import { Income } from "@/pages/Income"
import { UnapprovedInbox } from "@/pages/UnapprovedInbox"
import { MonthEnd } from "@/pages/MonthEnd"
import { FinancialHealth } from "@/pages/FinancialHealth"
import { useSyncStatus, useSyncNow, useVersionCheck, type FilterOption, type Page } from "@/lib/api"

// ---------------------------------------------------------------------------
// Nav section definitions (refs #186)
// ---------------------------------------------------------------------------

interface NavSection {
  label: string
  advancedOnly?: boolean
  items: NavItem[]
}

// ---------------------------------------------------------------------------
// View mode context
// ---------------------------------------------------------------------------

type ViewMode = "basic" | "advanced"

interface ViewModeContextValue {
  mode: ViewMode
  setMode: (m: ViewMode) => void
}

export const ViewModeContext = createContext<ViewModeContextValue>({
  mode: "basic",
  setMode: () => {},
})

export function useViewMode() {
  return useContext(ViewModeContext)
}

// ---------------------------------------------------------------------------
// Nav items
// ---------------------------------------------------------------------------

interface NavItem {
  icon: React.ComponentType<{ className?: string }>
  label: string
  page: Page | null
  advancedOnly?: boolean
}

// Nav sections in display order (refs #186)
const NAV_SECTIONS: NavSection[] = [
  {
    label: "Operational",
    items: [
      { icon: LayoutDashboard, label: "Overview", page: "overview" },
      { icon: Inbox, label: "Unapproved Inbox", page: "unapproved" as Page },
      { icon: Gauge, label: "Spending Pace", page: "spending-pace" as Page },
      { icon: Wallet, label: "Paycheck Funding", page: "paycheck-funding" as Page },
      { icon: CalendarDays, label: "Upcoming", page: "upcoming" as Page },
    ],
  },
  {
    label: "Monthly Remediation",
    items: [
      { icon: CheckSquare, label: "Month-End", page: "month-end" as Page },
      { icon: Coins, label: "Sinking Funds", page: "sinking-funds" as Page },
    ],
  },
  {
    label: "Strategic",
    advancedOnly: true,
    items: [
      { icon: DollarSign, label: "Income", page: "income" as Page, advancedOnly: true },
      { icon: TrendingUp, label: "Financial Health", page: "financial-health" as Page, advancedOnly: true },
      { icon: Target, label: "Target Calibration", page: "calibration" as Page, advancedOnly: true },
      { icon: GitBranch, label: "Two-Pot Compliance", page: "two-pot" as Page, advancedOnly: true },
    ],
  },
  {
    label: "Quarterly",
    advancedOnly: true,
    items: [
      { icon: Landmark, label: "Retirement", page: "retirement" as Page, advancedOnly: true },
      { icon: PieChart, label: "Net Worth", page: "net-worth" as Page, advancedOnly: true },
      { icon: CreditCard, label: "Subscriptions", page: "subscriptions" as Page, advancedOnly: true },
      { icon: Activity, label: "Funding Log", page: "churn" as Page, advancedOnly: true },
    ],
  },
]

// Utility section always shown at bottom, advanced-only items filtered by mode
const UTILITY_ITEMS: NavItem[] = [
  { icon: BarChart2, label: "Custom Report", page: "report" as Page, advancedOnly: true },
  { icon: ClipboardList, label: "Audit Log", page: "audit" as Page, advancedOnly: true },
  { icon: Settings, label: "Admin", page: "admin" as Page, advancedOnly: true },
]

// Flat lists for backward-compat helpers
const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items)

// Pages only reachable in advanced mode - used to redirect when switching to basic
const ADVANCED_ONLY_PAGES = new Set<Page>(
  [...NAV_ITEMS, ...UTILITY_ITEMS]
    .filter((item) => item.advancedOnly && item.page !== null)
    .map((item) => item.page as Page)
)

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatRelativeTime(isoUtc: string): string {
  const normalized = isoUtc.replace(/(Z|[+-]\d{2}:\d{2})$/, "") + "Z"
  const diff = Date.now() - new Date(normalized).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

// ---------------------------------------------------------------------------
// Query client
// ---------------------------------------------------------------------------

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 60_000,
      refetchOnWindowFocus: true,
    },
  },
})

// ---------------------------------------------------------------------------
// Inline view mode toggle (no external switch dependency required)
// ---------------------------------------------------------------------------

function ViewModeToggle() {
  const { mode, setMode } = useViewMode()
  const isAdvanced = mode === "advanced"

  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <span className={cn("text-xs", !isAdvanced ? "text-foreground font-medium" : "text-muted-foreground")}>
        Basic
      </span>
      <button
        role="switch"
        aria-checked={isAdvanced}
        onClick={() => setMode(isAdvanced ? "basic" : "advanced")}
        className={cn(
          "relative inline-flex h-4 w-7 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          isAdvanced ? "bg-primary" : "bg-input",
        )}
      >
        <span
          className={cn(
            "pointer-events-none block h-3 w-3 rounded-full bg-background shadow-lg ring-0 transition-transform",
            isAdvanced ? "translate-x-3" : "translate-x-0",
          )}
        />
      </button>
      <span className={cn("text-xs", isAdvanced ? "text-foreground font-medium" : "text-muted-foreground")}>
        Advanced
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------

interface SidebarProps {
  activePage: Page
  onNavigate: (page: Page) => void
}

function Sidebar({ activePage, onNavigate }: SidebarProps) {
  useVersionCheck()
  const { data: syncStatus } = useSyncStatus()
  const { mode } = useViewMode()
  const lastSynced = syncStatus?.last_synced_at
    ? formatRelativeTime(syncStatus.last_synced_at)
    : null
  const lastAutoSync = syncStatus?.last_auto_sync_at
    ? formatRelativeTime(syncStatus.last_auto_sync_at)
    : null
  const autoSyncInterval = syncStatus?.auto_sync_interval_minutes ?? 0

  const { mutate: syncNow, isPending: isSyncing } = useSyncNow()

  function handleSync() {
    syncNow(undefined, {
      onSuccess: () => toast.success("Sync complete"),
      onError: (err) => toast.error(`Sync failed: ${err.message}`),
    })
  }

  const visibleUtility = UTILITY_ITEMS.filter((item) => mode === "advanced" || !item.advancedOnly)

  function NavButton({ icon: Icon, label, page }: NavItem) {
    const isActive = page !== null && page === activePage
    const isDisabled = page === null
    return (
      <button
        key={label}
        onClick={() => {
          if (page !== null) onNavigate(page)
        }}
        disabled={isDisabled}
        className={cn(
          "w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
          isActive
            ? "bg-background text-foreground font-medium shadow-sm"
            : isDisabled
              ? "text-muted-foreground cursor-not-allowed opacity-60"
              : "text-muted-foreground hover:text-foreground hover:bg-background/60",
        )}
      >
        <Icon className="h-4 w-4 shrink-0" />
        {label}
      </button>
    )
  }

  // Filter sections and their items based on current view mode
  const visibleSections = NAV_SECTIONS.filter(
    (section) => mode === "advanced" || !section.advancedOnly,
  ).map((section) => ({
    ...section,
    items: section.items.filter((item) => mode === "advanced" || !item.advancedOnly),
  })).filter((section) => section.items.length > 0)

  return (
    <aside className="w-56 shrink-0 border-r bg-muted/30 flex flex-col">
      <div className="px-4 py-5 border-b">
        <h1 className="font-semibold text-sm tracking-tight">YNAB Dashboard</h1>
        <p className="text-xs text-muted-foreground">Local analytics</p>
      </div>
      <nav className="flex-1 px-2 py-3 overflow-y-auto">
        {visibleSections.map((section, sectionIdx) => (
          <div key={section.label} className={cn(sectionIdx > 0 && "mt-3")}>
            <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 select-none">
              {section.label}
            </p>
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavButton key={item.label} {...item} />
              ))}
            </div>
          </div>
        ))}
      </nav>
      {visibleUtility.length > 0 && (
        <div className="px-2 pb-1 border-t pt-2 space-y-0.5">
          <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 select-none">
            Utilities
          </p>
          {visibleUtility.map(({ icon: Icon, label, page }) => {
            const isActive = page !== null && page === activePage
            return (
              <button
                key={label}
                onClick={() => {
                  if (page !== null) onNavigate(page)
                }}
                className={cn(
                  "w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                  isActive
                    ? "bg-background text-foreground font-medium shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-background/60",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {label}
              </button>
            )
          })}
        </div>
      )}
      <div className="border-t">
        <ViewModeToggle />
      </div>
      <div className="px-4 py-3 border-t space-y-2">
        <button
          onClick={handleSync}
          disabled={isSyncing}
          className={cn(
            "w-full flex items-center justify-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium border transition-colors",
            isSyncing
              ? "opacity-60 cursor-not-allowed border-input bg-muted text-muted-foreground"
              : "border-input bg-background text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <RefreshCw className={cn("h-3 w-3", isSyncing && "animate-spin")} />
          {isSyncing ? "Syncing..." : "Sync Now"}
        </button>
        {lastSynced && (
          <p className="text-xs text-muted-foreground">
            Last sync: <span className="text-foreground">{lastSynced}</span>
          </p>
        )}
        {autoSyncInterval > 0 && (
          <p className="text-xs text-muted-foreground">
            Auto-sync:{" "}
            <span className="text-foreground">
              every {autoSyncInterval}m{lastAutoSync ? ` · ${lastAutoSync}` : ""}
            </span>
          </p>
        )}
      </div>
    </aside>
  )
}

// ---------------------------------------------------------------------------
// App root
// ---------------------------------------------------------------------------

// Page redirects: old pages that have been merged into new locations
const PAGE_REDIRECTS: Partial<Record<Page, Page>> = {
  "overspend-plan": "month-end",
  "trends": "financial-health",
  "budget-fit": "financial-health",
}

export default function App() {
  const [activePage, setActivePage] = useState<Page>("overview")
  const [calibrationFilter, setCalibrationFilter] = useState<FilterOption>("All")
  const [viewMode, setViewModeState] = useState<ViewMode>(() => {
    const stored = localStorage.getItem("ynab-view-mode")
    return stored === "advanced" ? "advanced" : "basic"
  })

  // Apply redirects for merged pages
  useEffect(() => {
    const redirect = PAGE_REDIRECTS[activePage]
    if (redirect) {
      setActivePage(redirect)
    }
  }, [activePage])

  function setViewMode(m: ViewMode) {
    setViewModeState(m)
    localStorage.setItem("ynab-view-mode", m)
    // Redirect to overview when switching to basic from an advanced-only page
    if (m === "basic" && ADVANCED_ONLY_PAGES.has(activePage)) {
      setActivePage("overview")
    }
  }

  function navigateToCalibration(filter: FilterOption = "All") {
    setCalibrationFilter(filter)
    setActivePage("calibration")
  }

  return (
    <QueryClientProvider client={queryClient}>
      <ViewModeContext.Provider value={{ mode: viewMode, setMode: setViewMode }}>
        <div className="flex h-screen bg-background text-foreground overflow-hidden">
          <Sidebar activePage={activePage} onNavigate={setActivePage} />
          <main className="flex-1 overflow-y-auto">
            <div className="max-w-5xl mx-auto px-6 py-6">
              {activePage === "overview" && <Overview onNavigate={setActivePage} />}
              {activePage === "month-end" && <MonthEnd />}
              {activePage === "unapproved" && <UnapprovedInbox />}
              {activePage === "financial-health" && <FinancialHealth onNavigateToCalibration={navigateToCalibration} />}
              {activePage === "calibration" && (
                <TargetCalibration key={calibrationFilter} initialFilter={calibrationFilter} />
              )}
              {activePage === "sinking-funds" && <SinkingFunds />}
              {activePage === "retirement" && <Retirement />}
              {activePage === "net-worth" && <NetWorth />}
              {activePage === "subscriptions" && <Subscriptions />}
              {activePage === "income" && <Income />}
              {activePage === "spending-pace" && <SpendingPace />}
              {activePage === "paycheck-funding" && <PaycheckFunding />}
              {activePage === "report" && <CustomReport />}
              {activePage === "upcoming" && <Upcoming />}
              {activePage === "audit" && <Audit />}
              {activePage === "churn" && <Churn onNavigate={setActivePage} />}
              {activePage === "two-pot" && <TwoPot />}
              {activePage === "admin" && <Admin />}
            </div>
          </main>
        </div>
      </ViewModeContext.Provider>
      <Toaster richColors position="bottom-right" />
    </QueryClientProvider>
  )
}
