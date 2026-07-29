import { useState } from "react"
import { ChevronDown, ChevronRight } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { KPICard } from "@/components/KPICard"
import { useSpendingPace, formatCurrency } from "@/lib/api"
import type { SpendingPaceCategory } from "@/lib/api"
import { cn } from "@/lib/utils"

function PaceBar({ spent, budgeted, pctElapsed }: { spent: number; budgeted: number; pctElapsed: number }) {
  const pctUsed = budgeted > 0 ? Math.min(spent / budgeted, 1) : 0
  const markerPct = Math.min(pctElapsed, 100)

  return (
    <div className="relative h-2 w-full rounded-full bg-muted overflow-visible">
      <div
        className={cn(
          "h-full rounded-full transition-all",
          pctUsed > 1 ? "bg-red-500" : pctUsed > 0.8 ? "bg-amber-500" : "bg-emerald-500",
        )}
        style={{ width: `${pctUsed * 100}%` }}
      />
      <div
        className="absolute top-1/2 -translate-y-1/2 w-0.5 h-3.5 bg-foreground/40 rounded-full"
        style={{ left: `${markerPct}%` }}
      />
    </div>
  )
}

function StatusBadge({ status }: { status: SpendingPaceCategory["status"] }) {
  if (status === "overspent") {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300">
        Overspent
      </span>
    )
  }
  if (status === "hot") {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300">
        Hot
      </span>
    )
  }
  if (status === "under") {
    return (
      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400">
        Under
      </span>
    )
  }
  return (
    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
      On Track
    </span>
  )
}

function AnomalyBadge({ label, zScore }: { label: string | null; zScore: number | null }) {
  if (!label) return null
  const text = zScore !== null && zScore !== undefined
    ? `${zScore.toFixed(1)}σ · ${label}`
    : label
  return (
    <span className="text-xs italic text-muted-foreground">
      {text}
    </span>
  )
}

function CategoryRow({ cat, pctElapsed }: { cat: SpendingPaceCategory; pctElapsed: number }) {
  const isUnbudgeted = cat.budgeted === 0
  const overage = cat.projected > cat.budgeted ? cat.projected - cat.budgeted : 0
  const hasTypical = cat.trailing_avg > 0
  const isNormalPattern =
    (cat.status === "hot" || cat.status === "overspent") &&
    hasTypical &&
    cat.projected <= cat.trailing_avg * 1.1

  return (
    <div className="py-3 border-b last:border-b-0">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium truncate">{cat.name}</span>
          <StatusBadge status={cat.status} />
          {isNormalPattern && (
            <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              within normal range
            </span>
          )}
          {(cat.status === "hot" || cat.status === "overspent") && (
            <AnomalyBadge label={cat.anomaly_label} zScore={cat.z_score} />
          )}
        </div>
        <div className="flex items-center gap-4 text-xs text-muted-foreground shrink-0 ml-3">
          {isUnbudgeted ? (
            <span className="text-rose-600 font-medium">{formatCurrency(cat.spent)} spent · $0 budgeted this month</span>
          ) : (
            <>
              <span>
                {formatCurrency(cat.spent)} spent · {formatCurrency(cat.budgeted)} budget
              </span>
              {overage > 0 && <span className="text-red-600 font-medium">+{formatCurrency(overage)} projected overage</span>}
            </>
          )}
        </div>
      </div>
      {!isUnbudgeted && <PaceBar spent={cat.spent} budgeted={cat.budgeted} pctElapsed={pctElapsed} />}
      {!isUnbudgeted && (
        <div className="flex justify-between mt-1 text-xs text-muted-foreground">
          <span>{Math.round(cat.pct_used)}% of budget</span>
          <div className="flex items-center gap-3">
            {hasTypical && (
              <span>3-month avg: {formatCurrency(cat.trailing_avg)}</span>
            )}
            <span>{cat.pace.toFixed(2)}x expected rate</span>
          </div>
        </div>
      )}
    </div>
  )
}

function CategorySection({
  title,
  categories,
  pctElapsed,
  emptyText,
  headerClass,
  defaultCollapsed = false,
}: {
  title: string
  categories: SpendingPaceCategory[]
  pctElapsed: number
  emptyText: string
  headerClass: string
  defaultCollapsed?: boolean
}) {
  const [open, setOpen] = useState(!defaultCollapsed)

  if (categories.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className={cn("text-base", headerClass)}>{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{emptyText}</p>
        </CardContent>
      </Card>
    )
  }

  if (defaultCollapsed) {
    return (
      <Card>
        <button
          onClick={() => setOpen((o) => !o)}
          className="w-full text-left"
        >
          <CardHeader className="pb-2">
            <CardTitle className={cn("text-base flex items-center justify-between", headerClass)}>
              <span>
                {title}{" "}
                <span className="font-normal text-muted-foreground">({categories.length} {categories.length === 1 ? "category" : "categories"} under-paced)</span>
              </span>
              {open ? (
                <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
              )}
            </CardTitle>
          </CardHeader>
        </button>
        {open && (
          <CardContent>
            {categories.map((cat) => (
              <CategoryRow key={cat.name} cat={cat} pctElapsed={pctElapsed} />
            ))}
          </CardContent>
        )}
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className={cn("text-base", headerClass)}>
          {title} <span className="font-normal text-muted-foreground">({categories.length})</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {categories.map((cat) => (
          <CategoryRow key={cat.name} cat={cat} pctElapsed={pctElapsed} />
        ))}
      </CardContent>
    </Card>
  )
}

export function SpendingPace() {
  const { data, isLoading, error } = useSpendingPace()

  if (isLoading) return <div className="text-sm text-muted-foreground">Loading...</div>
  if (error || !data) return <div className="text-sm text-destructive">Failed to load spending pace data.</div>

  const overspent = data.categories.filter((c) => c.status === "overspent")
  const hot = data.categories.filter((c) => c.status === "hot")
  const onTrack = data.categories.filter((c) => c.status === "on_track")
  const under = data.categories.filter((c) => c.status === "under")

  const daysLeft = data.days_in_month - data.days_elapsed
  const pctElapsedLabel = Math.round(data.pct_elapsed)

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Spending Pace</h2>
        <p className="text-sm text-muted-foreground">
          Day {data.days_elapsed} of {data.days_in_month} ({pctElapsedLabel}% elapsed, {daysLeft} days left)
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <KPICard
          label="Overspent"
          value={String(data.overspent_count)}
          subtitle="categories over budget"
          variant={data.overspent_count > 0 ? "negative" : "default"}
          tooltip="Categories where actual spending has already exceeded the budgeted amount this month."
        />
        <KPICard
          label="Running Hot"
          value={String(data.hot_count)}
          subtitle="on pace to overspend"
          variant={data.hot_count > 0 ? "negative" : "default"}
          tooltip="Categories not yet over budget but spending faster than expected. Projected to exceed budget by month end."
        />
        <KPICard
          label="On Track"
          value={String(data.on_track_count)}
          subtitle="within expected pace"
          variant="default"
          tooltip="Categories spending at a normal rate relative to how far through the month we are."
        />
        <KPICard
          label="Projected Overspend"
          value={formatCurrency(data.projected_overspend)}
          subtitle="estimated total overrun"
          variant={data.projected_overspend > 0 ? "negative" : "default"}
          tooltip="Sum of projected budget overages across all overspent and running-hot categories if current spending rates continue."
        />
      </div>

      <div className="text-xs text-muted-foreground flex items-center gap-2">
        <div className="w-8 h-1.5 bg-muted rounded-full relative">
          <div className="absolute top-1/2 -translate-y-1/2 w-0.5 h-3 bg-foreground/40 rounded-full" style={{ left: "50%" }} />
        </div>
        Vertical marker shows expected position at {pctElapsedLabel}% of month elapsed
      </div>

      <CategorySection
        title="Overspent"
        categories={overspent}
        pctElapsed={data.pct_elapsed}
        emptyText="No overspent categories."
        headerClass="text-rose-700 dark:text-rose-400"
      />
      <CategorySection
        title="Running Hot"
        categories={hot}
        pctElapsed={data.pct_elapsed}
        emptyText="No categories running hot."
        headerClass="text-red-700 dark:text-red-400"
      />
      <CategorySection
        title="On Track"
        categories={onTrack}
        pctElapsed={data.pct_elapsed}
        emptyText="No categories on track."
        headerClass="text-emerald-700 dark:text-emerald-400"
      />
      <CategorySection
        title="Under-paced"
        categories={under}
        pctElapsed={data.pct_elapsed}
        emptyText="No categories under-paced."
        headerClass="text-muted-foreground"
        defaultCollapsed
      />
    </div>
  )
}
