import { useState } from "react"
import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { KPICard } from "@/components/KPICard"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import type { ChartConfig } from "@/components/ui/chart"
import { useSubscriptions, formatCurrency, formatMonth } from "@/lib/api"
import type { SubscriptionItem } from "@/lib/api"
import { cn } from "@/lib/utils"

const trendChartConfig = {
  total: { label: "Charged", color: "oklch(0.6 0.18 250)" },
} satisfies ChartConfig

type SortKey = keyof Pick<
  SubscriptionItem,
  | "payee"
  | "category"
  | "frequency"
  | "median_amount"
  | "monthly_cost"
  | "last_charged"
  | "next_expected"
  | "annual_cost"
  | "status"
>
type SortDir = "asc" | "desc"

function SortableHead({
  label,
  col,
  current,
  dir,
  onSort,
  className,
}: {
  label: string
  col: SortKey
  current: SortKey
  dir: SortDir
  onSort: (col: SortKey) => void
  className?: string
}) {
  const active = col === current
  return (
    <TableHead className={cn("cursor-pointer select-none whitespace-nowrap", className)} onClick={() => onSort(col)}>
      <span className="inline-flex items-center gap-1">
        {label}
        {active ? (
          dir === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )
        ) : (
          <ChevronsUpDown className="h-3 w-3 opacity-30" />
        )}
      </span>
    </TableHead>
  )
}

function frequencyBadge(freq: SubscriptionItem["frequency"]) {
  const styles: Record<SubscriptionItem["frequency"], string> = {
    monthly: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
    quarterly: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
    annual: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300",
    irregular: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400",
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${styles[freq]}`}>
      {freq}
    </span>
  )
}

function statusBadge(status: SubscriptionItem["status"]) {
  if (status === "active") {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300">
        active
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
      check
    </span>
  )
}

function formatDate(iso: string): string {
  const [year, month, day] = iso.split("-")
  return new Date(Number(year), Number(month) - 1, Number(day)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  })
}

const selectCls = "text-xs border rounded px-2 py-1 bg-background text-foreground"

export function Subscriptions() {
  const { data, isLoading } = useSubscriptions()
  const [sortKey, setSortKey] = useState<SortKey>("monthly_cost")
  const [sortDir, setSortDir] = useState<SortDir>("desc")
  const [categoryFilter, setCategoryFilter] = useState("All")
  const [statusFilter, setStatusFilter] = useState("All")
  const [frequencyFilter, setFrequencyFilter] = useState("All")

  if (isLoading || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-xl font-semibold">Subscriptions</h2>
        <p className="text-muted-foreground text-sm">Loading...</p>
      </div>
    )
  }

  const { subscriptions, yoy_change, config_hint } = data

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const cutoff = new Date(today)
  cutoff.setDate(cutoff.getDate() + 14)

  const renewingSoon = subscriptions.filter((s) => {
    if (!s.next_expected) return false
    const [y, mo, dd] = s.next_expected.split("-").map(Number)
    const d = new Date(y, mo - 1, dd)
    return d >= today && d <= cutoff
  }).sort((a, b) => (a.next_expected ?? "").localeCompare(b.next_expected ?? ""))

  function handleSort(col: SortKey) {
    if (col === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(col)
      setSortDir("desc")
    }
  }

  const sorted = [...subscriptions].sort((a, b) => {
    // 'check' items always sort above 'active' items
    if (a.status !== b.status) {
      return a.status === "check" ? -1 : 1
    }
    if (sortKey === "next_expected") {
      if (!a.next_expected) return 1
      if (!b.next_expected) return -1
    }
    const aVal = a[sortKey] ?? ""
    const bVal = b[sortKey] ?? ""
    const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0
    return sortDir === "asc" ? cmp : -cmp
  })

  const yoyVariant =
    yoy_change === null ? "neutral" : yoy_change > 0 ? "negative" : yoy_change < 0 ? "positive" : "neutral"

  const visible = sorted.filter(
    (s) =>
      (categoryFilter === "All" || s.category.includes(categoryFilter)) &&
      (statusFilter === "All" || s.status === statusFilter) &&
      (frequencyFilter === "All" || s.frequency === frequencyFilter),
  )

  const filtersActive = categoryFilter !== "All" || statusFilter !== "All" || frequencyFilter !== "All"

  const visibleActive = visible.filter((s) => s.status === "active")
  const visibleCheck = visible.filter((s) => s.status === "check")
  const displayMonthly = Math.round(visibleActive.reduce((sum, s) => sum + s.monthly_cost, 0) * 100) / 100
  const displayAnnual = Math.round(displayMonthly * 12 * 100) / 100
  const displayActiveCount = visibleActive.length
  const displayCheckCount = visibleCheck.length

  const yoyLabel =
    yoy_change === null
      ? "no prior data"
      : yoy_change === 0
        ? "no change vs last year"
        : `${yoy_change > 0 ? "+" : ""}${formatCurrency(Math.abs(yoy_change))}/mo vs last year${filtersActive ? " (overall)" : ""}`

  const monthlyMap = new Map<string, number>()
  for (const sub of visible) {
    for (const { month, total } of sub.monthly_data) {
      monthlyMap.set(month, (monthlyMap.get(month) ?? 0) + total)
    }
  }
  const trendData = Array.from(monthlyMap.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, total]) => ({ month: formatMonth(month), total: Math.round(total * 100) / 100 }))

  const sh = { current: sortKey, dir: sortDir, onSort: handleSort }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Subscriptions</h2>
        <p className="text-sm text-muted-foreground">
          Detected from {data.detection_months} months of transaction history
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KPICard label="Monthly Cost" value={formatCurrency(displayMonthly)} variant="neutral" />
        <KPICard label="Annual Cost" value={formatCurrency(displayAnnual)} variant="neutral" />
        <KPICard
          label="Active"
          value={String(displayActiveCount)}
          variant="neutral"
          subtitle={displayCheckCount > 0 ? `${displayCheckCount} to check` : undefined}
        />
        <KPICard
          label="Year-over-Year Change"
          value={yoy_change === null ? "-" : `${yoy_change >= 0 ? "+" : ""}${formatCurrency(yoy_change)}`}
          variant={yoyVariant}
          subtitle={yoyLabel}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Category</span>
          <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} className={selectCls}>
            <option value="All">All</option>
            <option value="Personal">Personal</option>
            <option value="Business">Business</option>
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Status</span>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={selectCls}>
            <option value="All">All</option>
            <option value="active">Active</option>
            <option value="check">Check</option>
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Frequency</span>
          <select value={frequencyFilter} onChange={(e) => setFrequencyFilter(e.target.value)} className={selectCls}>
            <option value="All">All</option>
            <option value="monthly">Monthly</option>
            <option value="quarterly">Quarterly</option>
            <option value="annual">Annual</option>
            <option value="irregular">Irregular</option>
          </select>
        </div>
        {filtersActive && (
          <button
            onClick={() => { setCategoryFilter("All"); setStatusFilter("All"); setFrequencyFilter("All") }}
            className="text-xs text-muted-foreground hover:text-foreground underline"
          >
            Clear filters
          </button>
        )}
      </div>

      {renewingSoon.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30 px-4 py-3">
          <p className="text-sm font-semibold text-amber-900 dark:text-amber-300">
            {renewingSoon.length} {renewingSoon.length === 1 ? "subscription" : "subscriptions"} renewing in the next 14 days
          </p>
          <ul className="mt-2 space-y-1">
            {renewingSoon.map((s) => (
              <li key={s.payee} className="flex items-center justify-between text-xs text-amber-800 dark:text-amber-400">
                <span className="font-medium">{s.payee}</span>
                <span className="ml-4 tabular-nums">
                  {formatCurrency(s.median_amount)} &middot; {formatDate(s.next_expected!)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            Active Subscriptions
            {filtersActive && (
              <span className="ml-2 font-normal text-muted-foreground">
                ({visible.length} of {subscriptions.length})
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          {subscriptions.length === 0 ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">
              {config_hint ?? "No transactions found in Subscriptions categories."}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableHead label="Service" col="payee" {...sh} />
                  <SortableHead label="Category" col="category" {...sh} className="hidden sm:table-cell" />
                  <SortableHead label="Freq" col="frequency" {...sh} />
                  <SortableHead label="Amount" col="median_amount" {...sh} className="text-right" />
                  <SortableHead label="Monthly" col="monthly_cost" {...sh} className="hidden md:table-cell text-right" />
                  <SortableHead label="Last Charged" col="last_charged" {...sh} className="hidden lg:table-cell" />
                  <SortableHead label="Next Expected" col="next_expected" {...sh} className="hidden lg:table-cell" />
                  <SortableHead label="Annual" col="annual_cost" {...sh} className="hidden md:table-cell text-right" />
                  <SortableHead label="Status" col="status" {...sh} />
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((sub) => (
                  <TableRow key={sub.payee}>
                    <TableCell className="font-medium">{sub.payee}</TableCell>
                    <TableCell className="hidden sm:table-cell text-xs text-muted-foreground">
                      {sub.category.replace("Subscriptions ", "")}
                    </TableCell>
                    <TableCell>{frequencyBadge(sub.frequency)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatCurrency(sub.median_amount)}</TableCell>
                    <TableCell className="hidden md:table-cell text-right tabular-nums text-muted-foreground">
                      {sub.frequency !== "monthly" ? formatCurrency(sub.monthly_cost) : ""}
                    </TableCell>
                    <TableCell className="hidden lg:table-cell text-muted-foreground text-sm">
                      {formatDate(sub.last_charged)}
                    </TableCell>
                    <TableCell className="hidden lg:table-cell text-muted-foreground text-sm">
                      {sub.next_expected ? formatDate(sub.next_expected) : "-"}
                    </TableCell>
                    <TableCell className="hidden md:table-cell text-right tabular-nums text-muted-foreground">
                      {formatCurrency(sub.annual_cost)}
                    </TableCell>
                    <TableCell>{statusBadge(sub.status)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {trendData.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Monthly Spend (12 months)</CardTitle>
          </CardHeader>
          <CardContent>
            <ChartContainer config={trendChartConfig} className="min-h-[180px] w-full">
              <BarChart data={trendData} barGap={4}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="month" tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 12 }}
                  tickFormatter={(v: number) => `$${v.toFixed(0)}`}
                  width={48}
                />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Bar dataKey="total" fill="var(--color-total)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ChartContainer>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
