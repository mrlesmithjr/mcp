import { useState } from "react"
import { ChevronDown, ChevronRight, Info } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { useTwoPot, type TwoPotMonth } from "@/lib/api"
import { cn } from "@/lib/utils"

function fmt(value: number): string {
  return `$${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtSigned(value: number): string {
  const sign = value >= 0 ? "+" : "-"
  return `${sign}${fmt(value)}`
}

function formatMonth(iso: string): string {
  const [year, month] = iso.split("-")
  return new Date(Number(year), Number(month) - 1, 1).toLocaleString("en-US", {
    month: "long",
    year: "numeric",
  })
}

function HoldingTrajectory({
  start,
  end,
  warning,
}: {
  start: number | null
  end: number | null
  warning: boolean
}) {
  if (start === null && end === null) return <span className="text-muted-foreground text-xs">n/a</span>
  return (
    <span className="text-xs tabular-nums">
      {start !== null ? (
        <span className={cn(start >= 0 ? "text-green-700" : "text-red-600")}>{fmtSigned(start)}</span>
      ) : (
        <span className="text-muted-foreground">?</span>
      )}
      <span className="text-muted-foreground mx-1">→</span>
      {end !== null ? (
        <span className={cn(end >= 0 ? "text-green-700" : "text-red-600")}>{fmtSigned(end)}</span>
      ) : (
        <span className="text-muted-foreground">?</span>
      )}
      {warning && <span className="ml-1 text-amber-500" title="Holding buffer decreased this month">↓</span>}
    </span>
  )
}

function MiniCategoryTable({ rows }: { rows: { category_name: string; group: string | null; delta: number }[] }) {
  if (rows.length === 0) {
    return null
  }
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-border/40 last:border-0">
            <td className="py-1 pr-2 font-medium">{r.category_name}</td>
            <td className="py-1 pr-2 text-muted-foreground hidden sm:table-cell">{r.group ?? ""}</td>
            <td className="py-1 text-right tabular-nums">{fmt(r.delta)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function InfoPanel() {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-md border border-border bg-muted/30 text-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full px-4 py-2 text-left text-muted-foreground hover:text-foreground transition-colors"
      >
        <Info className="h-4 w-4 shrink-0" />
        <span>How does this work?</span>
        {open ? <ChevronDown className="h-3 w-3 ml-auto" /> : <ChevronRight className="h-3 w-3 ml-auto" />}
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3 text-xs text-muted-foreground">
          <p>
            The two-pot rule separates what your regular paycheck funds from what your bonus funds. Keeping these
            separate makes your budget predictable: regular operations should never depend on a bonus arriving.
          </p>
          <div className="space-y-1.5">
            <div>
              <span className="font-semibold text-foreground">Regular paycheck</span> covers month-to-month
              operations: bills, groceries, gas, debt payments, and all recurring expenses.
            </div>
            <div>
              <span className="font-semibold text-foreground">Bonus paycheck</span> funds forward only: Holding: Next
              Month (the float buffer), savings goals, sinking funds, retirement, and investments.
            </div>
          </div>
          <div className="space-y-1.5">
            <div>
              <span className="font-semibold text-green-700 dark:text-green-500">Correct funding:</span> bonus money
              went to savings and forward categories. The bonus did its job.
            </div>
            <div>
              <span className="font-semibold text-amber-700 dark:text-amber-400">Backwards funding:</span> bonus money
              went to a regular operating category (groceries, a bill, etc.). This means regular pay fell short of
              covering that category's target. To fix: reduce the target, or reclassify the category as bonus-funded.
            </div>
          </div>
          <div>
            <span className="font-semibold text-foreground">Holding buffer:</span> the Holding: Next Month category
            acts as a float. Bonus loads it at the end of one month so the next month starts pre-funded. The
            trajectory shows where the buffer started and ended in the bonus month.
          </div>
          <div>
            <span className="font-semibold text-foreground">Headroom</span> is regular pay minus the sum of all
            regular-pot category budgets for that month. Positive headroom means regular pay is sufficient. Negative
            headroom means the bonus is structurally required just to cover operating expenses, not a savings choice.
          </div>
        </div>
      )}
    </div>
  )
}

function MonthCard({ month }: { month: TwoPotMonth }) {
  const [expanded, setExpanded] = useState(false)
  const total = month.total_correct + month.workflow_gap
  const backwardsPercent = total > 0 ? (month.workflow_gap / total) * 100 : 0
  const { headroom } = month.structural_gap

  return (
    <div className="border rounded-lg overflow-hidden">
      {/* Summary row */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left p-4 flex flex-wrap items-center gap-x-6 gap-y-2 hover:bg-muted/30 transition-colors"
      >
        <span className="font-semibold text-sm w-32 shrink-0">{formatMonth(month.month)}</span>

        <span className="text-sm text-muted-foreground">
          Bonus portion:{" "}
          <span className="font-medium text-foreground">{fmtSigned(month.bonus_portion)}</span>
          <span className="text-xs ml-1 text-muted-foreground">(above regular pay, {month.bonus_paycheck_date})</span>
        </span>

        <span className="text-sm text-muted-foreground">
          Holding buffer:{" "}
          <HoldingTrajectory
            start={month.holding_start}
            end={month.holding_end}
            warning={month.holding_delta_warning}
          />
        </span>

        {/* Structural compliance badge (primary metric) */}
        <span
          className={cn(
            "text-sm font-medium",
            month.structural_backwards > 0 ? "text-amber-700" : "text-green-700",
          )}
        >
          Bonus covered operations:{" "}
          {month.structural_backwards > 0 ? fmt(Math.max(0, month.structural_backwards)) : "Clean"}
          <span className="text-xs ml-1 font-normal opacity-75">
            [{month.structural_is_exact ? "exact" : "est"}]
          </span>
        </span>

        {/* Funding sequence gap badge (secondary, ynab-tools-only) */}
        <span className={cn("text-sm ml-auto text-muted-foreground", month.workflow_gap > 0 ? "text-amber-600" : "text-green-700")}>
          Funding sequence gap{" "}
          <span className="text-xs italic text-muted-foreground">(approx)</span>
          {": "}
          {month.workflow_gap > 0 ? fmt(month.workflow_gap) : "$0.00"}
        </span>

        {/* Mini progress bar */}
        {total > 0 && (
          <div className="w-24 h-2 rounded-full bg-muted overflow-hidden shrink-0">
            <div
              className={cn("h-full rounded-full", backwardsPercent > 0 ? "bg-amber-400" : "bg-green-500")}
              style={{ width: `${Math.max(backwardsPercent, backwardsPercent === 0 ? 100 : 0)}%` }}
            />
          </div>
        )}

        <span className="text-muted-foreground text-xs">{expanded ? "▲" : "▼"}</span>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t p-4 space-y-4">
          {/* Headroom: moved to top as the most actionable metric (refs #202) */}
          <div className="bg-muted/30 rounded-lg p-3 space-y-1 text-sm">
            <p className="font-medium text-xs uppercase tracking-wide text-muted-foreground mb-2">
              Regular pay vs. obligations
            </p>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Regular pay / month</span>
              <span className="tabular-nums font-medium">{fmt(month.structural_gap.monthly_regular_pay)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Regular-pot budgeted</span>
              <span className="tabular-nums font-medium">{fmt(month.structural_gap.regular_pot_budgeted)}</span>
            </div>
            <div className="flex justify-between border-t border-border/40 pt-1 mt-1">
              <span className="text-muted-foreground">Headroom</span>
              <span className={cn("tabular-nums font-semibold", headroom >= 0 ? "text-green-700" : "text-red-600")}>
                {fmtSigned(headroom)}
              </span>
            </div>
            {headroom >= 0 ? (
              <p className="text-xs text-muted-foreground mt-1">
                Your regular pay covers all regular-pot category budgets with {fmtSigned(headroom)} to spare. The
                budget is self-sustaining without the bonus.
              </p>
            ) : (
              <p className="text-xs text-muted-foreground mt-1">
                Regular pay is {fmt(Math.abs(headroom))} short of covering all regular-pot category budgets. The bonus
                is structurally required for operations, not just savings.
              </p>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Correct bucket */}
            <div className="space-y-2">
              <div>
                <h4 className="text-sm font-semibold text-green-700">
                  Savings &amp; forward-funded (correct): {fmt(month.total_correct)}
                </h4>
                <p className="text-xs text-muted-foreground mt-0.5">
                  These categories are properly bonus-funded: savings, sinking funds, retirement, and Holding: Next
                  Month.
                </p>
              </div>
              {month.correct.length === 0 ? (
                <p className="text-xs text-muted-foreground italic">None recorded this month.</p>
              ) : (
                <MiniCategoryTable rows={month.correct} />
              )}
            </div>

            {/* Backwards bucket */}
            <div className="space-y-2">
              <div>
                <h4 className="text-sm font-semibold text-amber-700">
                  Post-bonus funding sequence gap (ynab-tools only):{" "}
                  {month.workflow_gap > 0 ? fmt(month.workflow_gap) : "$0.00"}
                </h4>
                <p className="text-xs text-muted-foreground mt-0.5 italic">
                  Counts ynab-tools funding operations only; may double-count categories that received multiple
                  top-ups.
                </p>
                {month.workflow_gap > 0 ? (
                  <p className="text-xs text-muted-foreground mt-0.5">
                    These regular-pot categories received bonus money. Each one means regular pay fell short of its
                    target. Fix: reduce the target or reclassify it as bonus-funded.
                  </p>
                ) : (
                  <p className="text-xs text-green-700 mt-0.5">
                    All bonus funding went to savings and forward categories.
                  </p>
                )}
              </div>
              {month.workflow_gap > 0 && <MiniCategoryTable rows={month.backwards} />}
            </div>
          </div>

          {/* Action callout when workflow_gap > 0 */}
          {month.workflow_gap > 0 && (
            <div className="rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
              {fmt(month.workflow_gap)} funding sequence gap{" "}
              <span className="italic text-xs">(approximate)</span>: bonus money was used to top up regular operating categories.
              This reflects ynab-tools funding log activity and may double-count categories that received multiple
              top-ups. For a precise figure, see the Bonus covered operations amount above. Consider whether any of the
              backwards-funded categories should have lower targets.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function TwoPot() {
  const { data, isLoading, error } = useTwoPot()

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Two-Pot Compliance</h2>
        <p className="text-muted-foreground text-sm">
          Did this month's bonus fund forward (savings, Holding) or backwards (operating expenses it should not have
          needed to cover)?
        </p>
      </div>

      <InfoPanel />

      {isLoading && (
        <div className="flex items-center justify-center h-64 text-muted-foreground">
          Loading compliance data...
        </div>
      )}

      {!isLoading && error && (
        <Alert variant="destructive">
          <AlertTitle>Failed to load two-pot data</AlertTitle>
          <AlertDescription>
            Make sure the API server is running and data is synced (<code>ynab sync</code>).
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !error && data && !data.config_ok && (
        <Alert>
          <AlertTitle>Configuration required</AlertTitle>
          <AlertDescription>
            Set <code>YNAB_BONUS_THRESHOLD</code> and <code>YNAB_REGULAR_PAY</code> in your config to enable this
            view.
            <br />
            <span className="text-xs text-muted-foreground mt-1 block">
              <code>YNAB_BONUS_THRESHOLD</code>: the dollar amount above which a paycheck is considered a bonus
              paycheck.
              <br />
              <code>YNAB_REGULAR_PAY</code>: your regular per-paycheck net amount.
            </span>
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !error && data?.config_ok && data.months.length === 0 && (
        <div className="rounded-lg border bg-muted/20 p-8 text-center">
          <p className="text-sm text-muted-foreground">No bonus paychecks found in the last 12 months.</p>
          <p className="text-xs text-muted-foreground mt-1">
            Check that <code>YNAB_BONUS_THRESHOLD</code> is set correctly and run <code>ynab sync</code>.
          </p>
        </div>
      )}

      {!isLoading && !error && data?.config_ok && data.months.length > 0 && (
        <div className="space-y-3">
          {data.months.map((m) => (
            <MonthCard key={m.month} month={m} />
          ))}
        </div>
      )}
    </div>
  )
}
