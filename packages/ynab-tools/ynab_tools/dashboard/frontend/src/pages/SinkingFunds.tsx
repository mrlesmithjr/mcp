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
import { useSinkingFunds, useFundGoals, useOverview, formatCurrency } from "@/lib/api"
import type { SinkingFundCategory, FundGoalsResult } from "@/lib/api"
import { cn } from "@/lib/utils"

type FilterOption = "All" | "Funded" | "Underfunded" | "Negative"

const FILTER_OPTIONS: FilterOption[] = ["All", "Funded", "Underfunded", "Negative"]

function StatusBadge({ status }: { status: SinkingFundCategory["status"] }) {
  if (status === "UNDERFUNDED")
    return <Badge variant="outline" className="bg-amber-100 text-amber-800 border-amber-200">Underfunded</Badge>
  if (status === "NEGATIVE")
    return <Badge variant="outline" className="bg-red-100 text-red-800 border-red-200">Negative</Badge>
  return <Badge variant="outline" className="bg-green-100 text-green-800 border-green-200">Funded</Badge>
}

export function SinkingFunds() {
  const { data, isLoading, error } = useSinkingFunds()
  const { data: overviewData } = useOverview()
  const [activeFilter, setActiveFilter] = useState<FilterOption>("All")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [preview, setPreview] = useState<FundGoalsResult | null>(null)
  const { mutate: fundGoals, isPending: isFunding } = useFundGoals()

  function handleFundAllClick() {
    if (!data) return
    const month = data.month
    fundGoals(
      { budget_month: month, dry_run: true },
      {
        onSuccess: (result) => {
          setPreview(result)
          setDialogOpen(true)
        },
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    )
  }

  function handleConfirmFund() {
    if (!data || !preview) return
    fundGoals(
      { budget_month: data.month, dry_run: false },
      {
        onSuccess: (result) => {
          setDialogOpen(false)
          setPreview(null)
          if (result.failures.length > 0) {
            toast.warning(
              `Funded ${result.funded_count} categories. ${result.failures.length} failed: ${result.failures.map((f) => f.name).join(", ")}`,
            )
          } else {
            toast.success(`Funded ${result.funded_count} categories (${formatCurrency(result.total_funded)})`)
          }
        },
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    )
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading sinking funds...
      </div>
    )
  }

  if (error || !data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Failed to load data</AlertTitle>
        <AlertDescription>
          Make sure the API server is running and data is synced (<code>ynab sync</code>).
        </AlertDescription>
      </Alert>
    )
  }

  const { summary, categories } = data

  const staleCategories = categories.filter((c) => c.is_stale)
  const rta = overviewData?.rta ?? null

  const filtered =
    activeFilter === "All"
      ? categories
      : categories.filter((c) => {
          if (activeFilter === "Funded") return c.status === "FUNDED"
          if (activeFilter === "Underfunded") return c.status === "UNDERFUNDED"
          return c.status === "NEGATIVE"
        })

  const sorted = [...filtered].sort((a, b) => {
    const order = { NEGATIVE: 0, UNDERFUNDED: 1, FUNDED: 2 }
    if (a.status !== b.status) return order[a.status] - order[b.status]
    if (a.status === "UNDERFUNDED") return b.goal_under_funded - a.goal_under_funded
    return b.balance - a.balance
  })

  const underfundedVariant = summary.underfunded_count > 0 ? "negative" : "positive"
  const negativeVariant = summary.negative_count > 0 ? "negative" : "positive"

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Sinking Funds</h2>
          <p className="text-muted-foreground text-sm">Goal category status for {data.month.slice(0, 7)}</p>
        </div>
      </div>

      <DialogRoot open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Fund All Goals</DialogTitle>
            {preview && (
              <DialogDescription>
                {preview.funded_count} categories need {formatCurrency(preview.total_funded)}.
                Available to assign: {formatCurrency(preview.rta)}.
              </DialogDescription>
            )}
          </DialogHeader>
          {preview?.rta_insufficient && (
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Ready to Assign ({formatCurrency(preview.rta)}) is less than the total needed ({formatCurrency(preview.total_funded)}). Proceeding will leave your budget over-allocated.
            </div>
          )}
          {preview && preview.categories.length > 0 && (
            <div className="max-h-60 overflow-y-auto space-y-1 text-sm">
              {preview.categories.map((cat) => (
                <div key={cat.name} className="flex justify-between py-1 border-b last:border-0">
                  <span className="text-foreground truncate mr-2">{cat.name}</span>
                  <span className="text-muted-foreground tabular-nums shrink-0">
                    +{formatCurrency(cat.amount)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {preview && preview.skipped.length > 0 && (
            <p className="text-xs text-muted-foreground mt-2">
              Skipping {preview.skipped.length} manually reduced {preview.skipped.length === 1 ? "category" : "categories"}.
            </p>
          )}
          <DialogFooter>
            <DialogClose>
              <Button variant="outline" size="sm">Cancel</Button>
            </DialogClose>
            <Button size="sm" onClick={handleConfirmFund} disabled={isFunding}>
              {isFunding ? "Funding..." : "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </DialogRoot>

      {/* Stale goal dates warning */}
      {staleCategories.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30 px-4 py-3">
          <p className="text-sm font-semibold text-amber-900 dark:text-amber-300">
            {staleCategories.length} {staleCategories.length === 1 ? "goal has" : "goals have"} past-due target dates. Update them in YNAB.
          </p>
          <ul className="mt-1.5 space-y-0.5">
            {staleCategories.map((c) => (
              <li key={c.name} className="text-xs text-amber-800 dark:text-amber-400">
                {c.name}
                {c.next_due_date && (
                  <span className="ml-1 text-amber-600 dark:text-amber-500">
                    (was {new Date(c.next_due_date.slice(0, 7) + "-02").toLocaleDateString("en-US", { month: "short", year: "numeric" })})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* So-what summary */}
      {summary.underfunded_count === 0 && summary.negative_count === 0 ? (
        <div className="rounded-lg border border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20 px-4 py-2.5 text-sm text-green-800 dark:text-green-300">
          All goals fully funded this month.
        </div>
      ) : (
        <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 px-4 py-2.5 text-sm text-amber-800 dark:text-amber-300">
          {summary.underfunded_count > 0 && (
            <span>Fund {formatCurrency(summary.total_needed)} to clear {summary.underfunded_count} underfunded {summary.underfunded_count === 1 ? "goal" : "goals"}.</span>
          )}
          {summary.negative_count > 0 && (
            <span className={summary.underfunded_count > 0 ? " ml-3" : ""}>{summary.negative_count} {summary.negative_count === 1 ? "category has" : "categories have"} a negative balance.</span>
          )}
        </div>
      )}

      <div className="grid grid-cols-4 gap-4">
        <KPICard
          label="Total Balance"
          value={formatCurrency(summary.total_balance)}
          variant="positive"
        />
        <KPICard
          label="Funded"
          value={`${summary.funded_count} categories`}
          variant="neutral"
          subtitle="at or above goal target"
        />
        <KPICard
          label="Underfunded"
          value={`${summary.underfunded_count} categories`}
          variant={underfundedVariant}
          subtitle={summary.total_needed > 0 ? `${formatCurrency(summary.total_needed)} needed` : "no shortfall this month"}
        />
        <KPICard
          label="Negative"
          value={`${summary.negative_count} categories`}
          variant={negativeVariant}
          subtitle="balance below zero"
        />
      </div>

      {/* RTA pre-flight + Fund All Goals */}
      {summary.underfunded_count > 0 && (
        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
          <div className="flex-1 text-sm">
            {rta === null ? null : rta >= summary.total_needed ? (
              <span className="text-green-700 dark:text-green-400">
                You have {formatCurrency(rta)} available, enough to fund all goals ({formatCurrency(summary.total_needed)}).
              </span>
            ) : rta > 0 ? (
              <span className="text-amber-700 dark:text-amber-400">
                You have {formatCurrency(rta)} available, {formatCurrency(summary.total_needed - rta)} short of full funding ({formatCurrency(summary.total_needed)}).
              </span>
            ) : (
              <span className="text-red-700 dark:text-red-400">
                No funds available to assign (RTA: {formatCurrency(rta)}).
              </span>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={isFunding}
            onClick={handleFundAllClick}
          >
            {isFunding ? "Loading..." : "Fund All Goals"}
          </Button>
        </div>
      )}

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

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Category</TableHead>
            <TableHead>Group</TableHead>
            <TableHead>Goal Type</TableHead>
            <TableHead>Deadline</TableHead>
            <TableHead className="text-right">Balance</TableHead>
            <TableHead className="text-right">Needed</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((cat) => (
            <TableRow key={`${cat.group}-${cat.name}`}>
              <TableCell className="font-medium">
                {cat.consistently_underfunded && (
                  <span
                    className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-rose-100 text-rose-700 text-xs font-bold mr-1.5 cursor-default"
                    title="Underfunded 3 or more consecutive months. Consider raising the goal target or reviewing spending in this category."
                  >
                    !
                  </span>
                )}
                {cat.name}
              </TableCell>
              <TableCell className="text-muted-foreground">{cat.group}</TableCell>
              <TableCell className="text-muted-foreground text-sm">{cat.goal_type_label}</TableCell>
              <TableCell className="text-sm tabular-nums">
                {cat.next_due_date ? (
                  <span className={cat.is_stale ? "text-rose-600 font-medium" : "text-muted-foreground"}>
                    {new Date(cat.next_due_date.slice(0, 7) + "-02").toLocaleDateString("en-US", { month: "short", year: "numeric" })}
                    {cat.is_stale && (
                      <span
                        className="ml-1.5 inline-flex items-center rounded-full px-1.5 py-0.5 text-xs font-medium bg-rose-100 text-rose-700"
                        title="This is a one-time goal whose target date has already passed. Update the goal in YNAB to a future date."
                      >
                        past due
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell className={cn("text-right tabular-nums", cat.balance < 0 ? "text-rose-600" : "")}>
                {formatCurrency(cat.balance)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {cat.goal_under_funded > 0
                  ? <span className="text-amber-600">{formatCurrency(cat.goal_under_funded)}</span>
                  : <span className="text-muted-foreground">-</span>}
              </TableCell>
              <TableCell>
                <StatusBadge status={cat.status} />
              </TableCell>
            </TableRow>
          ))}
          {sorted.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground py-8">
                No categories match this filter.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}
