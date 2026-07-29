import { useState } from "react"
import { AlertTriangle, ChevronDown, CheckCircle2, Tag, CheckSquare, TrendingDown, TrendingUp, Minus, Home } from "lucide-react"
import { toast } from "sonner"
import { LineChart, Line, BarChart, Bar, Cell, ResponsiveContainer, Tooltip } from "recharts"
import { Alert } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { KPICard } from "@/components/KPICard"
import { PrimaryActionBanner } from "@/components/PrimaryActionBanner"
import { useOverviewBundle, useApproveAll, formatCurrency } from "@/lib/api"
import { useViewMode } from "@/App"
import type {
  NeedsAttentionItem,
  NeedsAttentionData,
  AccountHealthData,
  SavingsProgressData,
  HomeSpendingData,
  HealthRatiosData,
  DebtTrendData,
  NetWorthTrendData,
  Page,
} from "@/lib/api"

function formatMonthLong(isoMonth: string): string {
  const [year, month] = isoMonth.split("-")
  return new Date(Number(year), Number(month) - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" })
}

function StrategicWidget({
  label,
  headline,
  detail,
  ctaLabel,
  ctaPage,
  onNavigate,
  variant = "neutral",
}: {
  label: string
  headline: string
  detail: string
  ctaLabel: string
  ctaPage: Page
  onNavigate: (page: Page) => void
  variant?: "positive" | "negative" | "neutral" | "warning"
}) {
  const headlineColor = {
    positive: "text-green-700 dark:text-green-400",
    negative: "text-red-700 dark:text-red-400",
    neutral: "text-foreground",
    warning: "text-amber-700 dark:text-amber-400",
  }[variant]

  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 pb-3">
        <p className={`text-lg font-semibold tabular-nums ${headlineColor}`}>{headline}</p>
        <p className="text-xs text-muted-foreground mt-0.5 min-h-[1rem]">{detail}</p>
        <button
          onClick={() => onNavigate(ctaPage)}
          className="mt-2 text-xs text-primary hover:underline underline-offset-2 focus:outline-none"
        >
          {ctaLabel}
        </button>
      </CardContent>
    </Card>
  )
}

function TxRow({ item }: { item: NeedsAttentionItem }) {
  const sign = item.amount < 0 ? "-" : "+"
  return (
    <div className="py-0.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted-foreground w-20 shrink-0">{item.date}</span>
        <span className="text-sm flex-1 min-w-0 truncate font-medium">{item.payee}</span>
        <span className="text-xs text-muted-foreground shrink-0 hidden sm:block">{item.account}</span>
        {item.category && (
          <span className="text-xs text-muted-foreground shrink-0 hidden md:block truncate max-w-[140px]">{item.category}</span>
        )}
        <span className="text-sm tabular-nums shrink-0">
          {sign === "-" ? "-" : ""}{formatCurrency(Math.abs(item.amount))}
        </span>
      </div>
      {item.memo && (
        <p className="text-xs text-muted-foreground pl-20 truncate italic">{item.memo}</p>
      )}
    </div>
  )
}

function NeedsAttentionSection({ data }: { data: NeedsAttentionData | null }) {
  const approveAll = useApproveAll()

  if (!data) return null

  if (data.total === 0) {
    return (
      <div className="rounded-lg border border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20 px-4 py-2.5 flex items-center gap-2">
        <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 shrink-0" />
        <span className="text-sm text-green-800 dark:text-green-300">Transaction inbox clear.</span>
      </div>
    )
  }

  function handleApproveAll() {
    approveAll.mutate(undefined, {
      onSuccess: (result) => {
        if (result.approved_count > 0) {
          toast.success(`Approved ${result.approved_count} transaction${result.approved_count === 1 ? "" : "s"}.`)
        }
        if (result.failed_count > 0) {
          toast.error(`${result.failed_count} transaction${result.failed_count === 1 ? "" : "s"} failed to approve.`)
        }
      },
      onError: (err) => toast.error(err.message),
    })
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          Needs Attention
          <span className="inline-flex items-center rounded-full bg-rose-100 dark:bg-rose-900/40 px-2 py-0.5 text-xs font-semibold text-rose-700 dark:text-rose-300">
            {data.total}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        {data.needs_category_count > 0 && (
          <div>
            <div className="flex items-center gap-1.5 mb-1.5">
              <Tag className="h-3.5 w-3.5 text-amber-600" />
              <span className="text-xs font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wide">
                Needs Category ({data.needs_category_count}
                {data.uncategorized_overspent_dollars != null && data.uncategorized_overspent_dollars > 0 && (
                  <span className="text-rose-700 dark:text-rose-400">
                    {" "}&middot; {formatCurrency(data.uncategorized_overspent_dollars)} overspent
                  </span>
                )}
                )
              </span>
            </div>
            <div className="space-y-0.5 pl-5">
              {data.needs_category.map((item) => (
                <TxRow key={item.id} item={item} />
              ))}
            </div>
            <p className="mt-1.5 pl-5 text-xs text-muted-foreground">
              Run <code className="text-xs bg-muted px-1 rounded">ynab categorize</code> or assign in the YNAB website.
            </p>
          </div>
        )}

        {data.ready_to_approve_count > 0 && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <div className="flex items-center gap-1.5">
                <CheckSquare className="h-3.5 w-3.5 text-blue-600" />
                <span className="text-xs font-semibold text-blue-700 dark:text-blue-400 uppercase tracking-wide">
                  Ready to Approve ({data.ready_to_approve_count})
                </span>
              </div>
              <button
                onClick={handleApproveAll}
                disabled={approveAll.isPending}
                className="text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed px-2.5 py-1 rounded transition-colors"
              >
                {approveAll.isPending ? "Approving..." : "Approve All"}
              </button>
            </div>
            <div className="space-y-0.5 pl-5">
              {data.ready_to_approve.map((item) => (
                <TxRow key={item.id} item={item} />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Account Health (reconciliation staleness + CC payment health + Two-Pot badge)
// ---------------------------------------------------------------------------
function AccountHealthRow({
  data,
  onNavigate,
  twoPotViolations,
  twoPotConfigured,
}: {
  data: AccountHealthData | null
  onNavigate: (page: Page) => void
  twoPotViolations: number | null
  twoPotConfigured: boolean
}) {
  if (!data) return null

  const reconcileOk = data.stale_count === 0
  const ccOk = data.cc_underfunded_count === 0
  const twoPotOk = twoPotViolations === 0

  return (
    <div className="flex flex-wrap gap-2 items-center">
      {/* Reconciliation pill */}
      {reconcileOk ? (
        <span className="inline-flex items-center gap-1 rounded-full bg-green-100 dark:bg-green-900/40 px-3 py-1 text-xs font-medium text-green-800 dark:text-green-300">
          <CheckCircle2 className="h-3 w-3" />
          All accounts current
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 dark:bg-amber-900/40 px-3 py-1 text-xs font-medium text-amber-800 dark:text-amber-300">
          <AlertTriangle className="h-3 w-3" />
          {data.stale_count} {data.stale_count === 1 ? "account" : "accounts"} stale
          {data.stale_accounts.length > 0 && (
            <span className="ml-1 text-amber-600 dark:text-amber-400">
              ({data.stale_accounts.slice(0, 2).map((a) => a.name).join(", ")}{data.stale_accounts.length > 2 ? ` +${data.stale_accounts.length - 2}` : ""})
            </span>
          )}
        </span>
      )}

      {/* CC payments pill */}
      {ccOk ? (
        <span className="inline-flex items-center gap-1 rounded-full bg-green-100 dark:bg-green-900/40 px-3 py-1 text-xs font-medium text-green-800 dark:text-green-300">
          <CheckCircle2 className="h-3 w-3" />
          CC payments funded
        </span>
      ) : (
        <span
          title={data.cc_underfunded.map((cc) => `${cc.account_name}: ${formatCurrency(Math.abs(cc.gap_dollars))} short`).join("\n")}
          className="inline-flex items-center gap-1 rounded-full bg-rose-100 dark:bg-rose-900/40 px-3 py-1 text-xs font-medium text-rose-800 dark:text-rose-300 cursor-help"
        >
          <AlertTriangle className="h-3 w-3" />
          {data.cc_underfunded_count} CC {data.cc_underfunded_count === 1 ? "card" : "cards"} underfunded
        </span>
      )}

      {/* Two-Pot compliance pill (only when configured) */}
      {twoPotConfigured && twoPotViolations !== null && (
        twoPotOk ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-100 dark:bg-green-900/40 px-3 py-1 text-xs font-medium text-green-800 dark:text-green-300">
            <CheckCircle2 className="h-3 w-3" />
            Two-Pot OK
          </span>
        ) : (
          <button
            onClick={() => onNavigate("two-pot")}
            className="inline-flex items-center gap-1 rounded-full bg-rose-100 dark:bg-rose-900/40 px-3 py-1 text-xs font-medium text-rose-800 dark:text-rose-300 hover:bg-rose-200 dark:hover:bg-rose-900/60 transition-colors"
          >
            <AlertTriangle className="h-3 w-3" />
            Two-Pot: {twoPotViolations} {twoPotViolations === 1 ? "violation" : "violations"}
          </button>
        )
      )}

      {(!reconcileOk || !ccOk) && (
        <button
          onClick={() => onNavigate("admin")}
          className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2"
        >
          Reconcile in YNAB
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 2026 Savings Progress card
// ---------------------------------------------------------------------------
function SavingsProgressCard({ data }: { data: SavingsProgressData | null | undefined }) {
  if (!data || data.items.length === 0) return null

  const yearElapsed = data.year_elapsed_pct

  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {data.year} Savings Progress
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 pb-3 space-y-2.5">
        {data.items.map((item) => {
          const pct = item.pct_complete
          const onTrack = pct >= yearElapsed
          const barColor = pct === 0
            ? "bg-rose-500 dark:bg-rose-600"
            : onTrack
              ? "bg-green-500 dark:bg-green-600"
              : "bg-amber-500 dark:bg-amber-600"
          const labelColor = pct === 0
            ? "text-rose-700 dark:text-rose-400"
            : onTrack
              ? "text-green-700 dark:text-green-400"
              : "text-amber-700 dark:text-amber-400"

          let actualLabel = ""
          let targetLabel = ""
          if (item.type === "balance_goal") {
            actualLabel = formatCurrency(item.current_balance ?? 0)
            targetLabel = formatCurrency(item.target ?? 0)
          } else if (item.type === "annual_contribution") {
            actualLabel = formatCurrency(item.ytd_contributed ?? 0)
            targetLabel = formatCurrency(item.annual_limit ?? 0)
          } else {
            actualLabel = formatCurrency(item.ytd_actual ?? 0)
            targetLabel = formatCurrency(item.ytd_target ?? 0)
          }

          return (
            <div key={item.name}>
              <div className="flex items-baseline justify-between gap-2 mb-0.5">
                <span className="text-xs text-muted-foreground truncate max-w-[150px]">{item.name}</span>
                <span className={`text-xs tabular-nums font-medium shrink-0 ${labelColor}`}>
                  {actualLabel} / {targetLabel}
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${barColor}`}
                  style={{ width: `${Math.min(pct, 100)}%` }}
                />
              </div>
              <div className="flex items-baseline justify-between mt-0.5">
                <span className={`text-xs ${labelColor}`}>{pct.toFixed(0)}%</span>
                {item.type === "balance_goal" && (item.monthly_needed ?? 0) > 0 && (
                  <span className="text-xs text-muted-foreground">{formatCurrency(item.monthly_needed!)}/mo needed</span>
                )}
                {item.type === "annual_contribution" && (
                  <span className="text-xs text-muted-foreground">{item.months_remaining}mo left</span>
                )}
              </div>
            </div>
          )
        })}
        <p className="text-xs text-muted-foreground pt-0.5">{yearElapsed.toFixed(0)}% of {data.year} elapsed</p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Home Spending card
// ---------------------------------------------------------------------------
function HomeSpendingCard({ data }: { data: HomeSpendingData | null | undefined }) {
  if (!data) return null

  const current = data.current_month.total_dollars
  const avg = data.three_month_avg
  const overAvg = current > avg && avg > 0

  const headlineColor = overAvg
    ? "text-amber-700 dark:text-amber-400"
    : "text-green-700 dark:text-green-400"

  // Build bar chart data: prior months + current
  const chartData = [
    ...data.prior_months.slice().reverse().map((m) => ({
      month: m.month.slice(0, 7),
      total: m.total_dollars,
      isCurrent: false,
    })),
    {
      month: data.current_month.month,
      total: current,
      isCurrent: true,
    },
  ]

  const top3 = data.current_month.categories.slice(0, 3)

  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-1">
          <Home className="h-3 w-3" />
          Home Spending
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0 pb-3">
        <div className="flex items-baseline gap-2">
          <p className={`text-lg font-semibold tabular-nums ${headlineColor}`}>
            {formatCurrency(current)}
          </p>
          <span className="text-xs text-muted-foreground">this month</span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          3-mo avg: {formatCurrency(avg)}
          {overAvg && (
            <span className="text-amber-600 dark:text-amber-400 ml-1">
              (+{formatCurrency(current - avg)} over avg)
            </span>
          )}
        </p>

        {chartData.some((d) => d.total > 0) && (
          <div className="mt-2 h-10">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} barSize={18}>
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const point = payload[0].payload as { month: string; total: number }
                      return (
                        <div className="rounded border bg-background px-2 py-1 text-xs shadow">
                          <p className="font-medium">{point.month}</p>
                          <p>{formatCurrency(point.total)}</p>
                        </div>
                      )
                    }
                    return null
                  }}
                />
                <Bar dataKey="total" radius={[2, 2, 0, 0]}>
                  {chartData.map((entry, idx) => (
                    <Cell
                      key={idx}
                      fill={entry.isCurrent
                        ? (overAvg ? "oklch(0.769 0.188 70.08)" : "oklch(0.627 0.194 149.214)")
                        : "oklch(0.6 0.1 240)"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {top3.length > 0 && (
          <div className="mt-2 space-y-0.5">
            {top3.map((c) => (
              <div key={c.name} className="flex justify-between text-xs">
                <span className="text-muted-foreground truncate max-w-[140px]">{c.name.replace("Home: ", "")}</span>
                <span className="tabular-nums shrink-0">{formatCurrency(c.spent)}</span>
              </div>
            ))}
          </div>
        )}

        {current === 0 && (
          <p className="text-xs text-muted-foreground mt-1">No home spending yet this month.</p>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Health Ratios card
// ---------------------------------------------------------------------------
function HealthRatiosCard({ data, onNavigate }: { data: HealthRatiosData | null | undefined; onNavigate: (page: Page) => void }) {
  if (!data) return null

  if (!data.configured) {
    return (
      <Card>
        <CardHeader className="pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Budget Health Ratios</CardTitle>
        </CardHeader>
        <CardContent className="pt-0 pb-3">
          <p className="text-xs text-muted-foreground">Configure YNAB_GROSS_SALARY to enable.</p>
          <button
            onClick={() => onNavigate("admin")}
            className="mt-1 text-xs text-primary hover:underline underline-offset-2"
          >
            Admin →
          </button>
        </CardContent>
      </Card>
    )
  }

  const statusIcon = (status: string) => {
    if (status === "ok") return <span className="text-green-600 dark:text-green-400 font-bold">&#10003;</span>
    if (status === "warning" || status === "under") return <span className="text-amber-500 dark:text-amber-400">&#9888;</span>
    return <span className="text-rose-600 dark:text-rose-400 font-bold">&#10005;</span>
  }

  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Budget Health Ratios</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 pb-3">
        <div className="space-y-1">
          {(data.ratios ?? []).map((r) => (
            <div key={r.label} className="flex items-center justify-between gap-2 text-xs">
              <span className="text-muted-foreground w-28 shrink-0">{r.label}</span>
              <span className="tabular-nums font-medium">{r.actual_pct.toFixed(1)}%</span>
              <span className="text-muted-foreground">
                {r.direction === "below" ? "<" : "≥"}{r.guideline_pct.toFixed(0)}%
              </span>
              <span>{statusIcon(r.status)}</span>
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          vs Money Guy guidelines · {data.month?.slice(0, 7)}
        </p>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Debt Trend card
// ---------------------------------------------------------------------------
function DebtTrendCard({ data }: { data: DebtTrendData | null | undefined }) {
  if (!data) return null

  const directionIcon =
    data.direction === "decreasing" ? (
      <TrendingDown className="h-4 w-4 text-green-600 dark:text-green-400" />
    ) : data.direction === "increasing" ? (
      <TrendingUp className="h-4 w-4 text-rose-600 dark:text-rose-400" />
    ) : (
      <Minus className="h-4 w-4 text-muted-foreground" />
    )

  const headlineColor =
    data.direction === "decreasing"
      ? "text-green-700 dark:text-green-400"
      : data.direction === "increasing"
      ? "text-rose-700 dark:text-rose-400"
      : "text-foreground"

  const deltaLabel =
    data.delta_dollars !== null
      ? `${data.delta_dollars >= 0 ? "+" : ""}${formatCurrency(data.delta_dollars)} vs prior snapshot`
      : "No prior snapshot"

  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Debt Trend</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 pb-3">
        <div className="flex items-center gap-1.5">
          {directionIcon}
          <p className={`text-lg font-semibold tabular-nums ${headlineColor}`}>
            {formatCurrency(data.total_debt_dollars)}
          </p>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">{deltaLabel}</p>
        {data.accounts.length > 0 && (
          <div className="mt-2 space-y-0.5">
            {data.accounts.slice(0, 4).map((a) => (
              <div key={a.name} className="flex justify-between text-xs">
                <span className="text-muted-foreground truncate max-w-[120px]">{a.name}</span>
                <span className="tabular-nums">{formatCurrency(a.balance_dollars)}</span>
              </div>
            ))}
            {data.accounts.length > 4 && (
              <p className="text-xs text-muted-foreground">+{data.accounts.length - 4} more</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Net Worth Trend card (sparkline)
// ---------------------------------------------------------------------------
function NetWorthTrendCard({ data }: { data: NetWorthTrendData | null | undefined }) {
  if (!data) return null

  if (!data.has_data || data.snapshots.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-1">
          <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Net Worth</CardTitle>
        </CardHeader>
        <CardContent className="pt-0 pb-3">
          <p className="text-xs text-muted-foreground">No data yet.</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Run <code className="text-xs bg-muted px-1 rounded">ynab net-worth</code> to start tracking.
          </p>
        </CardContent>
      </Card>
    )
  }

  const deltaColor =
    data.delta_from_last === null
      ? ""
      : data.delta_from_last >= 0
      ? "text-green-600 dark:text-green-400"
      : "text-rose-600 dark:text-rose-400"

  const deltaLabel =
    data.delta_from_last !== null
      ? `${data.delta_from_last >= 0 ? "+" : ""}${formatCurrency(data.delta_from_last)} vs ${data.delta_from_month ? formatMonthLong(data.delta_from_month) : "last snapshot"}`
      : ""

  const chartData = data.snapshots.map((s) => ({ month: s.month, value: s.net_worth }))

  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Net Worth</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 pb-3">
        <p className="text-lg font-semibold tabular-nums text-foreground">
          {formatCurrency(data.current_net_worth ?? 0)}
        </p>
        {deltaLabel && (
          <p className={`text-xs mt-0.5 ${deltaColor}`}>{deltaLabel}</p>
        )}
        {chartData.length >= 2 && (
          <div className="mt-2 h-10">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const point = payload[0].payload as { month: string; value: number }
                      return (
                        <div className="rounded border bg-background px-2 py-1 text-xs shadow">
                          <p className="font-medium">{point.month}</p>
                          <p>{formatCurrency(point.value)}</p>
                        </div>
                      )
                    }
                    return null
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="oklch(0.627 0.194 149.214)"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <p className="text-xs text-muted-foreground mt-1">{data.months_of_history}mo of history</p>
      </CardContent>
    </Card>
  )
}

export function Overview({ onNavigate }: { onNavigate: (page: Page) => void }) {
  const [selectedMonth, setSelectedMonth] = useState<string | undefined>(undefined)
  const [alertExpanded, setAlertExpanded] = useState(false)
  const { mode } = useViewMode()

  const { data: bundle, isLoading, error } = useOverviewBundle(selectedMonth)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading budget data...
      </div>
    )
  }

  if (error || !bundle?.overview) {
    return (
      <Alert variant="destructive">
        <h5 className="mb-1 font-medium leading-none tracking-tight">Failed to load data</h5>
        <div className="text-sm">
          Make sure the API server is running and data is synced (<code>ynab sync</code>).
        </div>
      </Alert>
    )
  }

  const overview = bundle.overview
  const months = bundle.months
  const budgetFit = bundle.budget_fit
  const sinkingFunds = bundle.sinking_funds
  const upcoming = bundle.upcoming
  const subscriptions = bundle.subscriptions
  const trends = bundle.trends
  const calibration = bundle.calibration
  const retirement = bundle.retirement
  const paycheckFunding = bundle.paycheck_funding
  const income = bundle.income
  const churn = bundle.churn
  const twoPot = bundle.two_pot
  const netWorthTrend = bundle.net_worth_trend

  const netVariant = overview.net >= 0 ? "positive" : "negative"
  const overspentCats = overview.running_hot.filter((c) => c.status === "OVERSPENT")
  const hotCats = overview.running_hot.filter((c) => c.status === "RUNNING_HOT")

  // Biggest overspend KPI (computed early, used in KPI row)
  const biggestOverspend = overspentCats.length > 0
    ? Math.max(...overspentCats.map((c) => c.budgeted > 0 ? c.spent - c.budgeted : c.spent))
    : 0
  const biggestOverspendName = overspentCats.length > 0
    ? overspentCats.reduce((a, b) => {
        const aAmt = a.budgeted > 0 ? a.spent - a.budgeted : a.spent
        const bAmt = b.budgeted > 0 ? b.spent - b.budgeted : b.spent
        return aAmt >= bAmt ? a : b
      }).name
    : ""

  // Net worth KPI (computed early, used in KPI row)
  const nwCurrent = netWorthTrend?.current_net_worth ?? null
  const nwDelta = netWorthTrend?.delta_from_last ?? null
  const nwDeltaMonth = netWorthTrend?.delta_from_month ?? null
  const nwValue = nwCurrent !== null ? formatCurrency(nwCurrent) : "No data"
  const nwSubtitle = nwDelta !== null
    ? `${nwDelta >= 0 ? "+" : ""}${formatCurrency(nwDelta)} vs ${nwDeltaMonth ? formatMonthLong(nwDeltaMonth) : "last snapshot"}`
    : nwCurrent !== null ? `${netWorthTrend?.months_of_history ?? 0}mo of history` : "Run ynab net-worth to start"
  const nwVariant: "positive" | "negative" | "neutral" =
    nwCurrent === null ? "neutral" : nwDelta !== null ? (nwDelta >= 0 ? "positive" : "negative") : "neutral"
  const alertTitle = [
    hotCats.length > 0 ? `${hotCats.length} running hot` : "",
    overspentCats.length > 0 ? `${overspentCats.length} overspent` : "",
  ].filter(Boolean).join(", ")

  // Budget health widget
  const headroomVariant = budgetFit
    ? budgetFit.headroom >= 0 ? "positive" : "negative"
    : "neutral"
  const budgetHeadline = budgetFit
    ? `${formatCurrency(budgetFit.headroom)} headroom`
    : "-"
  const calOpps = budgetFit
    ? (budgetFit.calibration_summary.over_target_count + budgetFit.calibration_summary.under_target_count)
    : 0
  const budgetDetail = budgetFit
    ? `${budgetFit.pct_committed.toFixed(1)}% of avg income committed${calOpps > 0 ? ` · ${calOpps} calibration ${calOpps === 1 ? "opportunity" : "opportunities"}` : ""}`
    : ""

  // Goals widget
  const goalsHeadline = sinkingFunds
    ? sinkingFunds.summary.underfunded_count === 0 && sinkingFunds.summary.negative_count === 0
      ? "All goals funded"
      : `${formatCurrency(sinkingFunds.summary.total_needed)} needed`
    : "-"
  const goalsDetail = sinkingFunds
    ? sinkingFunds.summary.underfunded_count === 0 && sinkingFunds.summary.negative_count === 0
      ? `${sinkingFunds.summary.funded_count} goals fully funded`
      : `${sinkingFunds.summary.underfunded_count} underfunded · ${sinkingFunds.summary.funded_count} funded`
    : ""
  const goalsVariant = sinkingFunds
    ? (sinkingFunds.summary.underfunded_count > 0 || sinkingFunds.summary.negative_count > 0) ? "warning" : "positive"
    : "neutral"

  // Upcoming widget
  const upcomingHeadline = upcoming
    ? upcoming.items.length === 0
      ? "Nothing due"
      : `${upcoming.items.length} item${upcoming.items.length === 1 ? "" : "s"}, ${formatCurrency(upcoming.total_amount)}`
    : "-"
  const upcomingDetail = upcoming && upcoming.items.length > 0
    ? upcoming.total_gap > 0
      ? `${formatCurrency(upcoming.total_gap)} unfunded across ${upcoming.unfunded_count} item${upcoming.unfunded_count === 1 ? "" : "s"}`
      : "All funded"
    : ""
  const upcomingVariant = upcoming && upcoming.total_gap > 0 ? "warning" : "neutral"

  // Subscriptions widget
  const subsHeadline = subscriptions ? formatCurrency(subscriptions.monthly_total) + "/mo" : "-"
  const subsDetail = subscriptions
    ? subscriptions.active_count === 0
      ? "No active subscriptions"
      : `${subscriptions.active_count} active${subscriptions.check_count > 0 ? ` · ${subscriptions.check_count} need review` : ""}${subscriptions.yoy_change !== null ? ` · ${subscriptions.yoy_change >= 0 ? "+" : ""}${formatCurrency(subscriptions.yoy_change)} vs last yr` : ""}`
    : ""
  const subsVariant: "warning" | "neutral" = (subscriptions?.check_count ?? 0) > 0 ? "warning" : "neutral"

  // Trends widget
  const trendStreak = trends?.streak
    ? `${trends.streak.count}-month ${trends.streak.type}`
    : "-"
  const trendDetail = trends
    ? `${formatCurrency(trends.avg_monthly_income)} avg income · ${formatCurrency(trends.avg_monthly_spending)} avg spending`
    : ""
  const trendVariant: "positive" | "negative" | "neutral" = trends?.streak?.type === "surplus" ? "positive"
    : trends?.streak?.type === "deficit" ? "negative"
    : "neutral"

  // Calibration widget
  const calMiscount = calibration
    ? calibration.over_target_count + calibration.under_target_count
    : 0
  const calHeadline = calibration
    ? calMiscount === 0
      ? "Targets aligned"
      : `${calMiscount} miscalibrated`
    : "-"
  const calDetail = calibration && calMiscount > 0
    ? `${calibration.net_headroom_impact >= 0 ? "+" : ""}${formatCurrency(calibration.net_headroom_impact)}/mo if applied`
    : ""
  const calVariant: "positive" | "warning" = calMiscount > 0 ? "warning" : "positive"

  // Retirement widget
  const retReadiness = retirement?.readiness ?? null
  const iraUnfunded =
    retirement?.configured === true &&
    (retirement?.contributions?.limits?.ira?.contributed ?? 1) === 0 &&
    (retirement?.contributions?.limits?.ira?.effective_limit ?? 0) > 0
  const retHeadline = !retirement?.configured
    ? "Not configured"
    : retReadiness
      ? `${retReadiness.multiplier.toFixed(1)}x salary`
      : "-"
  const retDetailParts: string[] = []
  if (retReadiness) {
    if (retReadiness.status === "ahead") retDetailParts.push(`${formatCurrency(retReadiness.gap)} ahead of target`)
    else if (retReadiness.status === "behind") retDetailParts.push(`${formatCurrency(Math.abs(retReadiness.gap))} behind target`)
    else retDetailParts.push("On track")
  }
  if (iraUnfunded) retDetailParts.push("IRA not yet funded this year")
  const retDetail = retDetailParts.join(" · ")
  const retVariant: "positive" | "negative" | "neutral" | "warning" = !retReadiness ? "neutral"
    : iraUnfunded ? "warning"
    : retReadiness.status === "ahead" ? "positive"
    : retReadiness.status === "behind" ? "negative"
    : "neutral"

  // Paycheck Funding widget
  const pfRta = paycheckFunding?.rta ?? null
  const pfNeeded = paycheckFunding?.total_needed ?? 0
  const pfProtectedTotal = paycheckFunding?.protected_total ?? 0
  const pfProtectedCovered = paycheckFunding?.is_protected_covered ?? (pfRta !== null && pfRta >= pfProtectedTotal)
  const pfHeadline = pfRta !== null ? `Ready to Assign: ${formatCurrency(pfRta)}` : "-"
  const pfDetailParts: string[] = []
  if (paycheckFunding) {
    if (pfNeeded === 0) {
      pfDetailParts.push("Nothing to fund")
    } else {
      pfDetailParts.push(
        pfProtectedCovered
          ? `T1-T3 covered (${formatCurrency(pfProtectedTotal)})`
          : `T1-T3 short - need ${formatCurrency(pfProtectedTotal)}`
      )
      if (pfNeeded > pfProtectedTotal) {
        pfDetailParts.push(`${formatCurrency(pfNeeded - pfProtectedTotal)} discretionary`)
      }
    }
  }
  const pfDetail = pfDetailParts.join(" · ")
  const pfVariant: "positive" | "negative" | "neutral" | "warning" =
    pfRta === null ? "neutral"
    : pfRta <= 0 ? "negative"
    : pfProtectedCovered ? "positive"
    : "warning"

  // T1-T3 KPI -- depends on pfRta / pfProtectedTotal declared above
  const t1t3Delta = pfRta !== null ? pfRta - pfProtectedTotal : null
  const t1t3Value = t1t3Delta !== null ? formatCurrency(Math.abs(t1t3Delta)) : "-"
  const t1t3Subtitle = t1t3Delta === null ? "" : t1t3Delta >= 0 ? "above essential obligations" : "short of essential obligations"
  const t1t3Variant: "positive" | "negative" | "neutral" =
    t1t3Delta === null ? "neutral" : t1t3Delta >= 0 ? "positive" : "negative"

  // Income widget
  const ytdTotal = income?.ytd_total ?? null
  const incomeHeadline = ytdTotal !== null ? formatCurrency(ytdTotal) : "-"
  const incomeYear = income?.current_year ?? new Date().getFullYear().toString()
  const incomeDetailParts: string[] = []
  if (income) {
    if (income.has_bonus_config) {
      incomeDetailParts.push(`Regular ${formatCurrency(income.ytd_regular)}`)
      if (income.ytd_bonus > 0) incomeDetailParts.push(`Bonus ${formatCurrency(income.ytd_bonus)}`)
    } else {
      incomeDetailParts.push(`Regular ${formatCurrency(income.ytd_regular)}`)
    }
    if (income.gross_salary && income.gross_salary > 0) {
      const pct = Math.round((income.ytd_total / income.gross_salary) * 100)
      incomeDetailParts.push(`${pct}% of target`)
    }
  }
  const incomeDetail = incomeDetailParts.join(" · ")
  const incomeVariant: "positive" | "neutral" = (income?.ytd_bonus ?? 0) > 0 ? "positive" : "neutral"

  // Two-Pot widget
  const lastBonusMonth = twoPot?.config_ok && twoPot.months.length > 0 ? twoPot.months[0] : null
  const twoPotHeadline = !twoPot
    ? "-"
    : !twoPot.config_ok
      ? "Not configured"
      : lastBonusMonth
        ? lastBonusMonth.workflow_gap === 0
          ? "Clean"
          : formatCurrency(lastBonusMonth.workflow_gap) + " backwards"
        : "No bonus months"
  const twoPotDetail = lastBonusMonth
    ? lastBonusMonth.workflow_gap === 0
      ? `${lastBonusMonth.month} bonus funded correctly`
      : `${lastBonusMonth.month} bonus topped up operations`
    : ""
  const twoPotVariant: "positive" | "warning" | "neutral" = !twoPot?.config_ok
    ? "neutral"
    : lastBonusMonth && lastBonusMonth.workflow_gap > 0
      ? "warning"
      : "positive"

  // Churn widget
  const churningCount = churn ? churn.rows.filter((r) => r.is_churning).length : null
  const churnHeadline = churningCount === null ? "-" : churningCount === 0 ? "No churn" : `${churningCount} churning`
  const churnDetail = churn
    ? churningCount === 0
      ? `${churn.rows.length} categories tracked (90d)`
      : `${churningCount} of ${churn.rows.length} categories need target increases`
    : ""
  const churnVariant: "positive" | "warning" | "neutral" = churningCount === null
    ? "neutral"
    : churningCount > 0
      ? "warning"
      : "positive"

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-baseline gap-3">
          <h2 className="text-2xl font-bold tracking-tight">{formatMonthLong(overview.month)}</h2>
          {months && months.months.length > 0 && (
            <select
              value={selectedMonth ?? ""}
              onChange={(e) => setSelectedMonth(e.target.value === "" ? undefined : e.target.value)}
              className="text-sm border rounded bg-background px-2 py-1 text-muted-foreground cursor-pointer focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="">Current month</option>
              {months.months.map((m) => (
                <option key={m} value={m}>
                  {formatMonthLong(m)}
                </option>
              ))}
            </select>
          )}
        </div>
        <p className="text-muted-foreground text-sm">
          Day {overview.days_elapsed} of {overview.days_in_month} ({overview.pct_elapsed}% of month elapsed)
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KPICard
          label="Cash Flow"
          tooltip="Income received minus actual spending this month (CC payments excluded to avoid double-counting)"
          value={formatCurrency(overview.net)}
          variant={netVariant}
          subtitle={`${formatCurrency(overview.income)} in · ${formatCurrency(overview.spending)} out`}
        />
        <KPICard
          label="Essential Coverage"
          tooltip="How much Ready to Assign (RTA) you have above or below your Tier 1-3 obligations - fixed bills, minimum debt payments, and required savings"
          value={t1t3Value}
          variant={t1t3Variant}
          subtitle={t1t3Subtitle}
        />
        <KPICard
          label={overspentCats.length > 0 ? "Biggest Overspend" : "Overspend"}
          tooltip="The category furthest over its monthly budget"
          value={biggestOverspend > 0 ? formatCurrency(biggestOverspend) : "None"}
          variant={biggestOverspend > 0 ? "negative" : "positive"}
          subtitle={biggestOverspend > 0 ? biggestOverspendName : "All categories ok"}
          badge={
            biggestOverspend > 0 && overview.biggest_overspend_anomaly_likely_one_time === true ? (
              <span className="inline-flex items-center rounded-full bg-sky-100 dark:bg-sky-900/40 px-2 py-0.5 text-xs font-medium text-sky-700 dark:text-sky-300 mt-1">
                likely one-time
              </span>
            ) : biggestOverspend > 0 &&
              overview.biggest_overspend_z_score !== null &&
              overview.biggest_overspend_z_score !== undefined &&
              overview.biggest_overspend_anomaly_likely_one_time === false ? (
              <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground mt-1">
                within pattern
              </span>
            ) : null
          }
        />
        <KPICard
          label="Net Worth"
          tooltip="Total assets minus liabilities across all tracked accounts"
          value={nwValue}
          variant={nwVariant}
          subtitle={nwSubtitle}
        />
      </div>

      {/* Primary action banner - synthesized recommendation (refs #187) */}
      <PrimaryActionBanner onNavigate={onNavigate} />

      {/* Account health: reconciliation staleness + CC payment status + Two-Pot badge */}
      <AccountHealthRow
        data={bundle.account_health}
        onNavigate={onNavigate}
        twoPotViolations={
          !twoPot?.config_ok
            ? null
            : twoPot.months.length === 0
              ? 0
              : (twoPot.months[0].structural_backwards > 0 || twoPot.months[0].workflow_gap > 0) ? 1 : 0
        }
        twoPotConfigured={twoPot?.config_ok ?? false}
      />

      {/* Transaction inbox */}
      <NeedsAttentionSection data={bundle.needs_attention} />

      {/* Priorities: all-clear or running-hot alert - moved above widgets for urgency */}
      {overview.priorities?.all_clear ? (
        <div className="rounded-lg border border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20 px-4 py-2.5 text-sm text-green-800 dark:text-green-300">
          All categories on track for today.
        </div>
      ) : null}
      {overview.running_hot.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              <div className="flex items-center justify-between w-full">
                <button
                  onClick={() => setAlertExpanded(!alertExpanded)}
                  className="flex items-center gap-2 text-left flex-1"
                >
                  <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
                  <span className="text-amber-800 dark:text-amber-400 font-medium">{alertTitle}</span>
                  <ChevronDown
                    className={`h-4 w-4 text-amber-600 transition-transform shrink-0 ml-1 ${alertExpanded ? "rotate-180" : ""}`}
                  />
                </button>
                <button
                  onClick={() => onNavigate("spending-pace")}
                  className="text-xs text-primary hover:underline underline-offset-2 shrink-0 ml-3"
                >
                  Spending Pace →
                </button>
              </div>
            </CardTitle>
          </CardHeader>
          {alertExpanded && (
            <CardContent className="space-y-3 pt-0">
              {overspentCats.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-rose-700 dark:text-rose-400 uppercase tracking-wide mb-1.5">
                    Overspent ({overspentCats.length})
                  </p>
                  <div className="space-y-1">
                    {overspentCats.map((cat) => (
                      <div key={cat.name} className="flex items-baseline justify-between gap-4">
                        <span className="text-sm text-rose-700 dark:text-rose-400 min-w-0">
                          <span className="text-xs opacity-50 mr-1">{cat.group}:</span>
                          <span className="font-medium">{cat.name}</span>
                        </span>
                        <span className="text-xs text-rose-700 dark:text-rose-400 whitespace-nowrap shrink-0 tabular-nums">
                          {cat.budgeted === 0
                            ? `${formatCurrency(cat.spent)} · $0 budgeted this month`
                            : `${formatCurrency(cat.spent)} / ${formatCurrency(cat.budgeted)} (${cat.pct_used.toFixed(0)}%)`}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {hotCats.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wide mb-1.5">
                    Running Hot ({hotCats.length})
                  </p>
                  <div className="space-y-1">
                    {hotCats.map((cat) => (
                      <div key={cat.name} className="flex items-baseline justify-between gap-4">
                        <span className="text-sm text-amber-700 dark:text-amber-300 min-w-0">
                          <span className="text-xs opacity-50 mr-1">{cat.group}:</span>
                          <span className="font-medium">{cat.name}</span>
                        </span>
                        <span className="text-xs text-amber-700 dark:text-amber-300 whitespace-nowrap shrink-0 tabular-nums">
                          {formatCurrency(cat.spent)} of {formatCurrency(cat.budgeted)} ({cat.pct_used.toFixed(0)}%) · proj. {formatCurrency(cat.projected)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          )}
        </Card>
      )}

      {/* Paycheck Funding (always) + Income (advanced) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <StrategicWidget
          label="Paycheck Funding"
          headline={pfHeadline}
          detail={pfDetail}
          ctaLabel="Paycheck Funding →"
          ctaPage="paycheck-funding"
          onNavigate={onNavigate}
          variant={pfVariant}
        />
        {mode === "advanced" && (
          <StrategicWidget
            label={`YTD Income (${incomeYear})`}
            headline={incomeHeadline}
            detail={incomeDetail}
            ctaLabel="Income →"
            ctaPage="income"
            onNavigate={onNavigate}
            variant={incomeVariant}
          />
        )}
      </div>

      {/* Strategic summary widgets */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {mode === "advanced" && (
          <StrategicWidget
            label="Budget Health"
            headline={budgetHeadline}
            detail={budgetDetail}
            ctaLabel="Budget Fit →"
            ctaPage="budget-fit"
            onNavigate={onNavigate}
            variant={headroomVariant}
          />
        )}
        <StrategicWidget
          label="Goals This Month"
          headline={goalsHeadline}
          detail={goalsDetail}
          ctaLabel="Sinking Funds →"
          ctaPage="sinking-funds"
          onNavigate={onNavigate}
          variant={goalsVariant}
        />
        <StrategicWidget
          label="Upcoming"
          headline={upcomingHeadline}
          detail={upcomingDetail}
          ctaLabel="Upcoming →"
          ctaPage="upcoming"
          onNavigate={onNavigate}
          variant={upcomingVariant}
        />
        {mode === "advanced" && (
          <StrategicWidget
            label="Subscriptions"
            headline={subsHeadline}
            detail={subsDetail}
            ctaLabel="Subscriptions →"
            ctaPage="subscriptions"
            onNavigate={onNavigate}
            variant={subsVariant}
          />
        )}
      </div>

      {/* Long-term health widgets - advanced mode only */}
      {mode === "advanced" && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <StrategicWidget
            label="Spending Trend"
            headline={trendStreak}
            detail={trendDetail}
            ctaLabel="Trends →"
            ctaPage="trends"
            onNavigate={onNavigate}
            variant={trendVariant}
          />
          <StrategicWidget
            label="Budget Targets"
            headline={calHeadline}
            detail={calDetail}
            ctaLabel="Calibration →"
            ctaPage="calibration"
            onNavigate={onNavigate}
            variant={calVariant}
          />
          <StrategicWidget
            label="Retirement"
            headline={retHeadline}
            detail={retDetail}
            ctaLabel="Retirement →"
            ctaPage="retirement"
            onNavigate={onNavigate}
            variant={retVariant}
          />
          <StrategicWidget
            label="Two-Pot Compliance"
            headline={twoPotHeadline}
            detail={twoPotDetail}
            ctaLabel="Two-Pot →"
            ctaPage="two-pot"
            onNavigate={onNavigate}
            variant={twoPotVariant}
          />
          <StrategicWidget
            label="Funding Churn (90d)"
            headline={churnHeadline}
            detail={churnDetail}
            ctaLabel="Churn Analysis →"
            ctaPage="churn"
            onNavigate={onNavigate}
            variant={churnVariant}
          />
        </div>
      )}

      {/* Savings Progress (always) + Home Spending (advanced) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SavingsProgressCard data={bundle.savings_progress} />
        {mode === "advanced" && <HomeSpendingCard data={bundle.home_spending} />}
      </div>

      {/* Ambient awareness widgets - advanced mode only */}
      {mode === "advanced" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <HealthRatiosCard data={bundle.health_ratios} onNavigate={onNavigate} />
          <DebtTrendCard data={bundle.debt_trend} />
          <NetWorthTrendCard data={bundle.net_worth_trend} />
        </div>
      )}

    </div>
  )
}
