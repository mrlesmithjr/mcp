import { useState } from "react"
import { toast } from "sonner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog"
import { KPICard } from "@/components/KPICard"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCalibration, useApplyTarget, formatCurrency, type CalibrationCategory, type FilterOption } from "@/lib/api"
import { cn } from "@/lib/utils"

const CALIBRATION_HINTS: Partial<Record<FilterOption, { heading: string; body: string; commands: string[] }>> = {
  "Over Target": {
    heading: "Goal set too low: spending exceeds it consistently.",
    body: "Raise the goal to match actual spending:",
    commands: [
      'ynab category set-goal "Category Name" <recommended_target>',
      "ynab fund --goals --apply   # fund all underfunded goals at once",
      'Raise the goal for "Category Name" to <recommended_target>   # via Claude',
    ],
  },
  "Under Target": {
    heading: "Goal set too high: money is being over-allocated.",
    body: "Lower the goal to free up budget elsewhere:",
    commands: [
      'ynab category set-goal "Category Name" <lower_amount>',
      "ynab fund --status          # review all targets vs actuals",
      'Lower the goal for "Category Name" to <lower_amount>   # via Claude',
    ],
  },
  "Unbudgeted": {
    heading: "No goal set: spending exists but it isn't tracked.",
    body: "Set a goal so this category can be funded and calibrated:",
    commands: [
      'ynab category set-goal "Category Name" 200',
      'Set a monthly goal of $200 for "Category Name"   # via Claude',
    ],
  },
}

function CalibrationHint({ filter }: { filter: FilterOption }) {
  const hint = CALIBRATION_HINTS[filter]
  if (!hint) return null
  return (
    <div className="rounded-lg border bg-muted/20 px-4 py-3 space-y-2">
      <p className="text-xs font-medium text-muted-foreground">{hint.heading}</p>
      <p className="text-xs text-muted-foreground">{hint.body}</p>
      <div className="space-y-1">
        {hint.commands.map((cmd, i) => (
          <code key={i} className="block text-xs bg-background border rounded px-2 py-1 text-foreground">
            {cmd}
          </code>
        ))}
      </div>
    </div>
  )
}
type SortKey = "current_target" | "median_monthly_spend" | "avg_monthly_spend" | "recommended_target" | "variance_pct"
type SortDir = "asc" | "desc" | "none"

interface SortState {
  key: SortKey | null
  dir: SortDir
}

const FILTER_OPTIONS: FilterOption[] = ["All", "Over Target", "Under Target", "On Target", "Unbudgeted"]

const STATUS_LABEL: Record<CalibrationCategory["status"], FilterOption> = {
  OVER_TARGET: "Over Target",
  UNDER_TARGET: "Under Target",
  ON_TARGET: "On Target",
  UNBUDGETED: "Unbudgeted",
}

function nextSortDir(current: SortDir): SortDir {
  if (current === "none") return "asc"
  if (current === "asc") return "desc"
  return "none"
}

function SortIndicator({ dir }: { dir: SortDir }) {
  if (dir === "asc") return <span className="ml-1 text-xs">↑</span>
  if (dir === "desc") return <span className="ml-1 text-xs">↓</span>
  return <span className="ml-1 text-xs text-muted-foreground">↕</span>
}

interface SortableHeadProps {
  label: string
  sortKey: SortKey
  sortState: SortState
  onSort: (key: SortKey) => void
  className?: string
}

function SortableHead({ label, sortKey, sortState, onSort, className }: SortableHeadProps) {
  const isActive = sortState.key === sortKey
  return (
    <TableHead
      className={cn("cursor-pointer select-none", className)}
      onClick={() => onSort(sortKey)}
    >
      <span className="inline-flex items-center">
        {label}
        <SortIndicator dir={isActive ? sortState.dir : "none"} />
      </span>
    </TableHead>
  )
}

function StatusBadge({ status }: { status: CalibrationCategory["status"] }) {
  if (status === "OVER_TARGET") {
    return (
      <Badge variant="outline" className="bg-red-100 text-red-800 border-red-200">
        Over Target
      </Badge>
    )
  }
  if (status === "UNDER_TARGET") {
    return (
      <Badge variant="outline" className="bg-blue-100 text-blue-800 border-blue-200">
        Under Target
      </Badge>
    )
  }
  if (status === "UNBUDGETED") {
    return (
      <Badge variant="outline" className="bg-gray-100 text-gray-600 border-gray-200">
        Unbudgeted
      </Badge>
    )
  }
  return (
    <Badge variant="outline" className="bg-green-100 text-green-800 border-green-200">
      On Target
    </Badge>
  )
}

function ConfidenceBadge({ confidence }: { confidence: CalibrationCategory["recommendation_confidence"] }) {
  if (confidence === "high") {
    return (
      <span
        title="High confidence: consistent spending pattern supports this recommendation"
        className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300 border border-green-200 dark:border-green-800 whitespace-nowrap"
      >
        high
      </span>
    )
  }
  if (confidence === "moderate") {
    return (
      <span
        title="Moderate confidence: some variance or anomalies detected, review before applying"
        className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300 border border-amber-200 dark:border-amber-800 whitespace-nowrap"
      >
        moderate
      </span>
    )
  }
  if (confidence === "low") {
    return (
      <span
        title="Low confidence: spending may be one-time or highly irregular"
        className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400 border border-gray-200 dark:border-gray-700 whitespace-nowrap"
      >
        low
      </span>
    )
  }
  return null
}

function sortCategories(
  categories: CalibrationCategory[],
  sortState: SortState,
): CalibrationCategory[] {
  if (sortState.key === null || sortState.dir === "none") return categories

  const key = sortState.key
  const multiplier = sortState.dir === "asc" ? 1 : -1

  return [...categories].sort((a, b) => {
    const aVal = key === "variance_pct" ? (a.variance_pct ?? 0) : (a[key] as number)
    const bVal = key === "variance_pct" ? (b.variance_pct ?? 0) : (b[key] as number)
    return (aVal - bVal) * multiplier
  })
}

export function TargetCalibration({ initialFilter = "All" }: { initialFilter?: FilterOption }) {
  const { data, isLoading, error } = useCalibration(12)
  const [activeFilter, setActiveFilter] = useState<FilterOption>(initialFilter)
  const [sortState, setSortState] = useState<SortState>({ key: null, dir: "none" })
  const { mutate: applyTarget, isPending: isApplying, variables: applyingVars } = useApplyTarget()
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean
    cat: CalibrationCategory | null
  }>({ open: false, cat: null })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading calibration data...
      </div>
    )
  }

  if (error || !data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Failed to load calibration data</AlertTitle>
        <AlertDescription>
          Make sure the API server is running and data is synced (<code>ynab sync</code>).
        </AlertDescription>
      </Alert>
    )
  }

  function handleSort(key: SortKey) {
    setSortState((prev) => {
      if (prev.key !== key) return { key, dir: "asc" }
      const dir = nextSortDir(prev.dir)
      return dir === "none" ? { key: null, dir: "none" } : { key, dir }
    })
  }

  const filtered =
    activeFilter === "All"
      ? data.categories
      : data.categories.filter((c) => STATUS_LABEL[c.status] === activeFilter)

  const sorted = sortCategories(filtered, sortState)

  function handleConfirmApply() {
    const cat = confirmDialog.cat
    if (!cat || !cat.id || !cat.goal_type) return
    applyTarget(
      {
        category_id: cat.id,
        new_target: cat.recommended_target,
        category_name: cat.name,
        category_group: cat.group,
        goal_type: cat.goal_type,
        goal_target_month: cat.goal_target_month ?? null,
        budget_month: cat.budget_month,
      },
      {
        onSuccess: () => toast.success(`Updated ${cat.name} to ${formatCurrency(cat.recommended_target)}`),
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    )
    setConfirmDialog({ open: false, cat: null })
  }

  return (
    <div className="space-y-6">
      <DialogRoot open={confirmDialog.open} onOpenChange={(open) => !open && setConfirmDialog({ open: false, cat: null })}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply Recommended Target</DialogTitle>
            {confirmDialog.cat && (
              <DialogDescription>
                Set <strong>{confirmDialog.cat.name}</strong> target to{" "}
                {formatCurrency(confirmDialog.cat.recommended_target)}?
              </DialogDescription>
            )}
          </DialogHeader>
          <DialogFooter>
            <DialogClose>
              <Button variant="outline" size="sm">Cancel</Button>
            </DialogClose>
            <Button size="sm" onClick={handleConfirmApply} disabled={isApplying}>
              {isApplying ? "Applying..." : "Apply"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </DialogRoot>

      <div>
        <h2 className="text-2xl font-bold tracking-tight">Target Calibration</h2>
        <p className="text-muted-foreground text-sm">
          {data.months_analyzed}-month spending averages vs current budget targets
        </p>
      </div>

      {/* KPI stat row */}
      <div className="grid grid-cols-4 gap-4">
        <KPICard
          label="Over Target"
          value={`${data.over_target_count} categories`}
          variant="negative"
          subtitle="goal set too low"
        />
        <KPICard
          label="On Target"
          value={`${data.on_target_count} categories`}
          variant="positive"
          subtitle="within range of goal"
        />
        <KPICard
          label="Under Target"
          value={`${data.under_target_count} categories`}
          variant="neutral"
          subtitle="consistently underspent"
        />
        <KPICard
          label="Unbudgeted"
          value={`${data.unbudgeted_count} categories`}
          variant="default"
          subtitle="no goal set"
        />
      </div>

      {/* Aggregate impact summary */}
      {(data.over_target_count > 0 || data.under_target_count > 0) && (
        <div className="rounded-lg border bg-muted/20 px-4 py-3 text-sm">
          <span className="text-muted-foreground">Applying all recommendations would </span>
          {data.net_headroom_impact >= 0 ? (
            <span className="font-medium text-green-700 dark:text-green-400">
              free up {formatCurrency(data.net_headroom_impact)}/mo
            </span>
          ) : (
            <span className="font-medium text-red-700 dark:text-red-400">
              cost {formatCurrency(Math.abs(data.net_headroom_impact))}/mo more
            </span>
          )}
          <span className="text-muted-foreground">
            {" "}(save {formatCurrency(data.potential_savings)} from under-target, add {formatCurrency(data.required_additions)} for over-target).
          </span>
        </div>
      )}

      {/* Filter bar */}
      <div className="flex gap-2">
        {FILTER_OPTIONS.map((option) => {
          const isActive = activeFilter === option
          return (
            <button
              key={option}
              onClick={() => setActiveFilter(option)}
              className={cn(
                "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                isActive
                  ? "bg-foreground text-background"
                  : "border border-input bg-background text-muted-foreground hover:bg-muted",
              )}
            >
              {option}
            </button>
          )
        })}
      </div>

      <CalibrationHint filter={activeFilter} />

      {/* Calibration table */}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead title="Recommendation confidence based on spending consistency">Confidence</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Group</TableHead>
            <SortableHead
              label="Current Target"
              sortKey="current_target"
              sortState={sortState}
              onSort={handleSort}
              className="text-right"
            />
            <SortableHead
              label="Median Spend"
              sortKey="median_monthly_spend"
              sortState={sortState}
              onSort={handleSort}
              className="text-right"
            />
            <SortableHead
              label={`Mean Spend (${data.months_analyzed}mo)`}
              sortKey="avg_monthly_spend"
              sortState={sortState}
              onSort={handleSort}
              className="text-right"
            />
            <TableHead className="text-right" title="Months with any spending activity in the analysis window">Active Months</TableHead>
            <TableHead className="text-right" title="Months where spending exceeded the budget target, out of active months">Over Target</TableHead>
            <SortableHead
              label="Recommended"
              sortKey="recommended_target"
              sortState={sortState}
              onSort={handleSort}
              className="text-right"
            />
            <SortableHead
              label="Variance"
              sortKey="variance_pct"
              sortState={sortState}
              onSort={handleSort}
              className="text-right"
            />
            <TableHead>Status</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((cat) => {
            const varianceText =
              cat.variance_pct != null
                ? `${cat.variance_pct > 0 ? "+" : ""}${cat.variance_pct.toFixed(1)}%`
                : "-"
            const varianceColor =
              cat.variance_pct == null
                ? "text-muted-foreground"
                : cat.variance_pct > 0
                  ? "text-red-600"
                  : "text-blue-600"

            const recChanged = cat.recommended_target !== cat.current_target
            const recColor = !recChanged
              ? "text-foreground"
              : cat.recommended_target > cat.current_target
                ? "text-amber-600"
                : "text-blue-600"

            const isHighVariance = cat.spending_pattern === "high_variance" || cat.spending_pattern === "lumpy"
            const medianTitle = isHighVariance
              ? `High variance (CV: ${cat.cv.toFixed(2)} - spending is irregular, so the median understates typical spend; mean is ${formatCurrency(cat.avg_monthly_spend)})`
              : cat.spending_pattern === "moderate_variance"
                ? `Moderate spending variance (CV: ${cat.cv.toFixed(2)} - coefficient of variation measures how consistent spending is month to month)`
                : undefined

            return (
              <TableRow key={`${cat.group}-${cat.name}`}>
                <TableCell>
                  <ConfidenceBadge confidence={cat.recommendation_confidence} />
                </TableCell>
                <TableCell className="font-medium">{cat.name}</TableCell>
                <TableCell className="text-muted-foreground">{cat.group}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatCurrency(cat.current_target)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  <span
                    className={cn(isHighVariance && "text-muted-foreground italic")}
                    title={medianTitle}
                  >
                    {isHighVariance ? "~" : ""}{formatCurrency(cat.median_monthly_spend)}
                  </span>
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {formatCurrency(cat.avg_monthly_spend)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {cat.months_active}
                </TableCell>
                <TableCell
                  className={`text-right tabular-nums ${cat.months_over_target >= cat.months_active * 0.75 ? "text-red-600 font-medium" : cat.months_over_target > 0 ? "text-amber-600" : "text-muted-foreground"}`}
                  title={`Over budget ${cat.months_over_target} of ${cat.months_active} active months`}
                >
                  {cat.months_over_target} / {cat.months_active}
                </TableCell>
                <TableCell className={cn("text-right tabular-nums", recColor)}>
                  {formatCurrency(cat.recommended_target)}
                </TableCell>
                <TableCell className={cn("text-right tabular-nums", varianceColor)}>
                  {varianceText}
                </TableCell>
                <TableCell>
                  <StatusBadge status={cat.status} />
                </TableCell>
                <TableCell>
                  {cat.id && cat.goal_type && cat.status !== "ON_TARGET" && cat.recommended_target !== cat.current_target ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={isApplying && applyingVars?.category_id === cat.id}
                      onClick={() => setConfirmDialog({ open: true, cat })}
                      className={cn(
                        cat.recommendation_confidence === "low" && "opacity-50",
                      )}
                    >
                      Apply
                    </Button>
                  ) : (
                    <span className="text-xs text-muted-foreground">-</span>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
          {sorted.length === 0 && (
            <TableRow>
              <TableCell colSpan={12} className="text-center text-muted-foreground py-8">
                No categories match this filter.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}
