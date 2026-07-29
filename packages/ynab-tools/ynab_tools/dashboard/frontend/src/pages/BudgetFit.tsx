import { useState } from "react"
import { AlertTriangle, ChevronDown, ChevronUp } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { KPICard } from "@/components/KPICard"
import { useBudgetFit, formatCurrency } from "@/lib/api"
import type { BudgetFitGroup, FilterOption } from "@/lib/api"
import { cn } from "@/lib/utils"

const GROUP_COLORS = [
  "oklch(0.627 0.194 149.214)",
  "oklch(0.637 0.237 25.331)",
  "oklch(0.6 0.18 220)",
  "oklch(0.65 0.2 60)",
  "oklch(0.55 0.22 300)",
  "oklch(0.62 0.18 35)",
  "oklch(0.58 0.19 170)",
  "oklch(0.60 0.21 280)",
]

type SortKey = "total_target" | "pct_of_income" | "category_count"
type SortDir = "asc" | "desc"

function SortIndicator({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="ml-1 text-xs text-muted-foreground">↕</span>
  return <span className="ml-1 text-xs">{dir === "asc" ? "↑" : "↓"}</span>
}

function GroupStatusBadge({
  group,
  onNavigate,
}: {
  group: BudgetFitGroup
  onNavigate: (filter: FilterOption) => void
}) {
  if (group.over_target_count > 0) {
    return (
      <button onClick={() => onNavigate("Over Target")} className="focus:outline-none">
        <Badge variant="outline" className="bg-red-100 text-red-800 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-800 cursor-pointer hover:opacity-80">
          {group.over_target_count} over
        </Badge>
      </button>
    )
  }
  if (group.under_target_count > 0) {
    return (
      <button onClick={() => onNavigate("Under Target")} className="focus:outline-none">
        <Badge variant="outline" className="bg-blue-100 text-blue-800 border-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-800 cursor-pointer hover:opacity-80">
          {group.under_target_count} under
        </Badge>
      </button>
    )
  }
  return (
    <Badge variant="outline" className="bg-green-100 text-green-800 border-green-200 dark:bg-green-950 dark:text-green-300 dark:border-green-800">
      On target
    </Badge>
  )
}

export function BudgetFit({ onNavigateToCalibration }: { onNavigateToCalibration: (filter: FilterOption) => void }) {
  const { data, isLoading, error } = useBudgetFit(12)
  const [calibrationOpen, setCalibrationOpen] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>("total_target")
  const [sortDir, setSortDir] = useState<SortDir>("desc")

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground text-sm">
        Loading budget fit data...
      </div>
    )
  }

  if (error || !data) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Failed to load budget fit data</AlertTitle>
        <AlertDescription>
          Make sure the API server is running and data is synced (<code>ynab sync</code>).
        </AlertDescription>
      </Alert>
    )
  }

  const {
    avg_monthly_income,
    total_current_targets,
    headroom,
    pct_committed,
    groups,
    calibration_summary: cal,
    analysis_months,
  } = data

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDir("desc")
    }
  }

  const sortedGroups = [...groups].sort((a, b) => {
    const mult = sortDir === "asc" ? 1 : -1
    return (a[sortKey] - b[sortKey]) * mult
  })

  const headroomVariant = headroom >= 0 ? "positive" : "negative"

  const pctSubtitle =
    pct_committed > 100
      ? "of avg income committed (over budget)"
      : pct_committed > 90
      ? "of avg income committed (nearly full)"
      : "of avg income committed"

  // When targets fit within income: domain = income so the headroom segment fills to the right edge.
  // When targets exceed income: domain = targets with a small buffer to show the overflow.
  const chartDomain =
    avg_monthly_income > 0 && total_current_targets <= avg_monthly_income
      ? avg_monthly_income
      : Math.max(total_current_targets * 1.04, 1)

  const incomePctOfDomain = avg_monthly_income > 0 && chartDomain > 0
    ? (avg_monthly_income / chartDomain) * 100
    : 0
  const headroomPctOfDomain = headroom > 0 && chartDomain > 0
    ? (headroom / chartDomain) * 100
    : 0

  const netDeltaPositive = cal.net_headroom_change >= 0

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Budget Fit</h2>
        <p className="text-muted-foreground text-sm">
          How your targets fit your income, based on {analysis_months} months of history
        </p>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-4">
        <KPICard
          label="Avg Monthly Income"
          value={formatCurrency(avg_monthly_income)}
          subtitle={`${analysis_months}-month average`}
          variant="positive"
        />
        <KPICard
          label="Total Targets"
          value={formatCurrency(total_current_targets)}
          subtitle={`${groups.length} category groups`}
          variant="neutral"
        />
        <KPICard
          label="Headroom"
          value={formatCurrency(headroom)}
          subtitle={headroom >= 0 ? "available after targets" : "over income"}
          variant={headroomVariant}
        />
        <KPICard
          label="% Committed"
          value={`${pct_committed.toFixed(1)}%`}
          subtitle={pctSubtitle}
          variant="neutral"
        />
      </div>

      {/* Calibration impact collapsible */}
      <div className="rounded-lg border bg-muted/20">
        <button
          onClick={() => setCalibrationOpen((o) => !o)}
          className="w-full flex items-center justify-between px-4 py-3 text-left"
        >
          <div className="space-y-0.5">
            <p className="text-sm font-medium">Calibration Impact</p>
            <p className="text-xs text-muted-foreground">
              <button
                onClick={(e) => { e.stopPropagation(); onNavigateToCalibration("Over Target") }}
                className="underline-offset-2 hover:underline focus:outline-none"
              >
                {cal.over_target_count} over-target
              </button>
              {" (need +"}
              {formatCurrency(cal.required_additions)}/mo if raised){" | "}
              <button
                onClick={(e) => { e.stopPropagation(); onNavigateToCalibration("Under Target") }}
                className="underline-offset-2 hover:underline focus:outline-none"
              >
                {cal.under_target_count} under-target
              </button>
              {" (save "}
              {formatCurrency(cal.potential_savings)}/mo if lowered)
            </p>
          </div>
          {calibrationOpen ? (
            <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
        </button>
        {calibrationOpen && (
          <div className="px-4 pb-4 space-y-3 border-t pt-3">
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <p className="text-xs text-muted-foreground mb-0.5">
                  Over-target categories
                </p>
                <p className="font-medium text-red-700 dark:text-red-400">
                  +{formatCurrency(cal.required_additions)}/mo needed
                </p>
                <p className="text-xs text-muted-foreground">{cal.over_target_count} categories</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground mb-0.5">
                  Under-target categories
                </p>
                <p className="font-medium text-green-700 dark:text-green-400">
                  -{formatCurrency(cal.potential_savings)}/mo savings
                </p>
                <p className="text-xs text-muted-foreground">{cal.under_target_count} categories</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground mb-0.5">Net headroom change</p>
                <p
                  className={cn(
                    "font-medium",
                    netDeltaPositive
                      ? "text-green-700 dark:text-green-400"
                      : "text-red-700 dark:text-red-400",
                  )}
                >
                  {netDeltaPositive ? "+" : "-"}
                  {formatCurrency(Math.abs(cal.net_headroom_change))}/mo
                </p>
              </div>
            </div>
            <div className="rounded-md bg-background border px-3 py-2 text-sm">
              <span className="text-muted-foreground">
                Applying all recommendations would change headroom from{" "}
              </span>
              <span
                className={cn(
                  "font-medium",
                  headroom >= 0 ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400",
                )}
              >
                {formatCurrency(headroom)}
              </span>
              <span className="text-muted-foreground"> to </span>
              <span
                className={cn(
                  "font-medium",
                  cal.recommended_headroom >= 0
                    ? "text-green-700 dark:text-green-400"
                    : "text-red-700 dark:text-red-400",
                )}
              >
                {formatCurrency(cal.recommended_headroom)}
              </span>
              <span className="text-muted-foreground">.</span>
            </div>
            {cal.unbudgeted_avg_spend > 0 && (
              <p className="text-xs text-muted-foreground">
                Untracked: categories with no target average{" "}
                {formatCurrency(cal.unbudgeted_avg_spend)}/mo in activity.
              </p>
            )}
            <div className="flex justify-end pt-1">
              <button
                onClick={() => onNavigateToCalibration("All")}
                className="text-xs text-primary hover:underline underline-offset-2 focus:outline-none"
              >
                Review in Target Calibration →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Stacked bar chart */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Target Allocation by Group</CardTitle>
        </CardHeader>
        <CardContent>
          {/* Bar: target segments + headroom segment (or overflow line) */}
          <div className="relative pb-6 pt-1">
            <div className="relative h-10 flex rounded-md overflow-hidden bg-muted/20">
              {/* Target group segments */}
              {groups.map((g, i) => {
                const widthPct = chartDomain > 0 ? (g.total_target / chartDomain) * 100 : 0
                return (
                  <div
                    key={g.name}
                    title={`${g.name}: ${formatCurrency(g.total_target)}`}
                    style={{
                      width: `${widthPct}%`,
                      backgroundColor: GROUP_COLORS[i % GROUP_COLORS.length],
                      flexShrink: 0,
                    }}
                  />
                )
              })}

              {/* Headroom segment: fills from end of targets to income (when targets fit) */}
              {headroom > 0 && headroomPctOfDomain > 0 && (
                <div
                  title={`Headroom: ${formatCurrency(headroom)}`}
                  style={{ width: `${headroomPctOfDomain}%`, flexShrink: 0 }}
                  className="bg-green-100 dark:bg-green-950/60 border-l-2 border-dashed border-green-400 dark:border-green-700 flex items-center justify-center overflow-hidden"
                >
                  {headroomPctOfDomain > 8 && (
                    <span className="text-xs font-medium text-green-700 dark:text-green-400 px-2 truncate">
                      {formatCurrency(headroom)} headroom
                    </span>
                  )}
                </div>
              )}

              {/* Dashed income line when targets exceed income */}
              {headroom < 0 && avg_monthly_income > 0 && incomePctOfDomain <= 100 && (
                <div
                  className="absolute top-0 bottom-0 border-l-2 border-dashed border-foreground/50"
                  style={{ left: `${incomePctOfDomain}%` }}
                />
              )}
            </div>

            {/* Scale: $0 on left, income (or max target) on right */}
            <div className="absolute bottom-0 left-0 right-0 flex justify-between text-xs text-muted-foreground">
              <span>$0</span>
              <span>{formatCurrency(chartDomain)}</span>
            </div>
          </div>

          {/* Color legend */}
          <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-1">
            {groups.map((g, i) => (
              <div key={g.name} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <div
                  className="h-2 w-2 rounded-sm shrink-0"
                  style={{ backgroundColor: GROUP_COLORS[i % GROUP_COLORS.length] }}
                />
                {g.name}
              </div>
            ))}
          </div>

          {headroom < 0 && avg_monthly_income > 0 && (
            <div className="mt-3 rounded-md border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/40 px-3 py-2 text-xs text-red-800 dark:text-red-300">
              Total targets ({formatCurrency(total_current_targets)}) exceed average income (
              {formatCurrency(avg_monthly_income)}) by {formatCurrency(Math.abs(headroom))}.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Group breakdown table */}
      <div>
        <h3 className="text-sm font-medium mb-3">Breakdown by Group</h3>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Group</TableHead>
              <TableHead
                className="text-right cursor-pointer select-none"
                onClick={() => handleSort("total_target")}
              >
                <span className="inline-flex items-center justify-end w-full">
                  Target Total
                  <SortIndicator active={sortKey === "total_target"} dir={sortDir} />
                </span>
              </TableHead>
              <TableHead
                className="text-right cursor-pointer select-none"
                onClick={() => handleSort("pct_of_income")}
              >
                <span className="inline-flex items-center justify-end w-full">
                  % of Income
                  <SortIndicator active={sortKey === "pct_of_income"} dir={sortDir} />
                </span>
              </TableHead>
              <TableHead
                className="text-right cursor-pointer select-none"
                onClick={() => handleSort("category_count")}
              >
                <span className="inline-flex items-center justify-end w-full">
                  Categories
                  <SortIndicator active={sortKey === "category_count"} dir={sortDir} />
                </span>
              </TableHead>
              <TableHead>Calibration Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedGroups.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                  No category groups found.
                </TableCell>
              </TableRow>
            ) : (
              sortedGroups.map((g) => (
                <TableRow key={g.name}>
                  <TableCell className="font-medium">{g.name}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(g.total_target)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {g.pct_of_income.toFixed(1)}%
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {g.category_count}
                  </TableCell>
                  <TableCell>
                    <GroupStatusBadge group={g} onNavigate={onNavigateToCalibration} />
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

