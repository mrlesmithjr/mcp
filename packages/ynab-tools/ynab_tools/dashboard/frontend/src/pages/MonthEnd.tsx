import { useState } from "react"
import { ChevronDown, ChevronUp } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { KPICard } from "@/components/KPICard"
import { useMonthEnd, useOverspendPlan, formatCurrency, type MonthEndOverspentCategory } from "@/lib/api"
import type { OverspendPlanCategory, OverspendDonor } from "@/lib/api"
import { cn } from "@/lib/utils"

function OverspentTable({
  categories,
  dimmed = false,
}: {
  categories: MonthEndOverspentCategory[]
  dimmed?: boolean
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead className="hidden sm:table-cell">Group</TableHead>
          <TableHead className="text-right">Budgeted</TableHead>
          <TableHead className="text-right">Activity</TableHead>
          <TableHead className="text-right">Balance</TableHead>
          <TableHead className="hidden md:table-cell" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {categories.map((cat) => (
          <TableRow key={cat.name} className={cn(dimmed && "opacity-70")}>
            <TableCell className="font-medium">{cat.name}</TableCell>
            <TableCell className="hidden sm:table-cell text-sm text-muted-foreground">
              {cat.group ?? ""}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm">
              {formatCurrency(cat.budgeted)}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm text-red-600 dark:text-red-400">
              {formatCurrency(cat.activity)}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm font-medium text-red-600 dark:text-red-400">
              {formatCurrency(cat.balance)}
            </TableCell>
            <TableCell className="hidden md:table-cell text-right">
              <div className="flex flex-col items-end gap-1">
                {cat.structural && (
                  <span className="text-xs font-medium text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded px-1.5 py-0.5">
                    structural
                  </span>
                )}
                {cat.anomaly_label && (
                  <span className="text-xs italic text-muted-foreground">
                    {cat.z_score !== null && cat.z_score !== undefined
                      ? `${cat.z_score.toFixed(1)}σ · ${cat.anomaly_label}`
                      : cat.anomaly_label}
                  </span>
                )}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function formatDate(iso: string): string {
  const [year, month, day] = iso.split("-")
  return new Date(Number(year), Number(month) - 1, Number(day)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  })
}

function formatMonth(yyyyMm: string): string {
  const [year, month] = yyyyMm.split("-")
  return new Date(Number(year), Number(month) - 1, 1).toLocaleString("en-US", {
    month: "long",
    year: "numeric",
  })
}

// ---------------------------------------------------------------------------
// Overspend coverage components (inlined from OverspendPlan page)
// ---------------------------------------------------------------------------

const CLASSIFICATION_COLORS: Record<string, string> = {
  STRUCTURAL:
    "text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-950/40 border-red-200 dark:border-red-800",
  SEASONAL:
    "text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-800",
  "ONE-OFF":
    "text-sky-700 dark:text-sky-400 bg-sky-50 dark:bg-sky-950/40 border-sky-200 dark:border-sky-800",
  "TIMING FLOAT":
    "text-violet-700 dark:text-violet-400 bg-violet-50 dark:bg-violet-950/40 border-violet-200 dark:border-violet-800",
}

function ClassificationBadge({ label }: { label: string }) {
  const colors =
    CLASSIFICATION_COLORS[label] ??
    "text-muted-foreground bg-muted border-input"
  return (
    <span
      className={cn(
        "text-xs font-medium border rounded px-1.5 py-0.5 whitespace-nowrap",
        colors,
      )}
    >
      {label}
    </span>
  )
}

function CoveragePlanTable({ categories }: { categories: OverspendPlanCategory[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead className="text-right">Budgeted</TableHead>
          <TableHead className="text-right">Activity</TableHead>
          <TableHead className="text-right">Overspent</TableHead>
          <TableHead className="hidden md:table-cell">Classification</TableHead>
          <TableHead className="hidden lg:table-cell">Detail</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {categories.map((cat) => (
          <TableRow key={cat.name}>
            <TableCell className="font-medium">{cat.name}</TableCell>
            <TableCell className="text-right tabular-nums text-sm">
              {formatCurrency(cat.budgeted)}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm text-red-600 dark:text-red-400">
              {formatCurrency(cat.activity)}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm font-semibold text-red-600 dark:text-red-400">
              {formatCurrency(cat.overspent)}
            </TableCell>
            <TableCell className="hidden md:table-cell">
              <ClassificationBadge label={cat.classification} />
            </TableCell>
            <TableCell className="hidden lg:table-cell text-xs text-muted-foreground max-w-[220px] truncate">
              {cat.detail}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function DonorTable({ donors }: { donors: OverspendDonor[] }) {
  if (donors.length === 0) {
    return (
      <p className="px-6 py-4 text-sm text-muted-foreground">
        No donor categories available. All discretionary balances are zero or negative.
      </p>
    )
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead className="hidden sm:table-cell">Group</TableHead>
          <TableHead className="text-right">Available</TableHead>
          <TableHead className="text-right">Suggested Transfer</TableHead>
          <TableHead className="hidden md:table-cell" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {donors.map((donor) => (
          <TableRow key={donor.name}>
            <TableCell className="font-medium">{donor.name}</TableCell>
            <TableCell className="hidden sm:table-cell text-sm text-muted-foreground">
              {donor.group}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm text-green-600 dark:text-green-400">
              {formatCurrency(donor.available)}
            </TableCell>
            <TableCell className="text-right tabular-nums text-sm font-semibold">
              {donor.suggested_transfer > 0 ? (
                <span className="text-amber-600 dark:text-amber-400">
                  {formatCurrency(donor.suggested_transfer)}
                </span>
              ) : (
                <span className="text-muted-foreground">-</span>
              )}
            </TableCell>
            <TableCell className="hidden md:table-cell text-right">
              {donor.is_sinking_fund && (
                <span className="text-xs font-medium text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-950/40 border border-violet-200 dark:border-violet-800 rounded px-1.5 py-0.5">
                  sinking fund
                </span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function OverspendCoverageSection() {
  const [open, setOpen] = useState(false)
  const { data, isLoading, error } = useOverspendPlan()

  if (isLoading || error || !data || data.overspent_count === 0) return null

  return (
    <div className="rounded-lg border bg-muted/20">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <div className="space-y-0.5">
          <p className="text-sm font-medium">Overspend Coverage</p>
          <p className="text-xs text-muted-foreground">
            {data.overspent_count} {data.overspent_count === 1 ? "category" : "categories"} overspent
            {" "}&middot;{" "}
            {data.fully_coverable
              ? `fully coverable from ${formatCurrency(data.total_coverable)} in budget`
              : `${formatCurrency(data.gap)} gap after donors`}
          </p>
        </div>
        {open ? (
          <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="border-t px-0 pb-4 space-y-4 pt-4">
          {/* KPI summary */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 px-4">
            <KPICard
              label="Total Overspent"
              value={formatCurrency(data.total_overspent)}
              variant="negative"
              subtitle={`${data.overspent_count} categor${data.overspent_count === 1 ? "y" : "ies"}`}
            />
            <KPICard
              label="Coverable from Budget"
              value={formatCurrency(data.total_coverable)}
              variant={data.fully_coverable ? "positive" : "negative"}
              subtitle={data.fully_coverable ? "fully covered by donors" : `${formatCurrency(data.gap)} gap remains`}
            />
            <KPICard
              label="Remaining Gap"
              value={formatCurrency(data.gap)}
              variant={data.gap === 0 ? "positive" : "negative"}
              subtitle={data.gap === 0 ? "fully coverable" : "cover with Ready to Assign"}
            />
          </div>

          {/* One-time overspends */}
          {data.one_time_overspends.length > 0 && (
            <Card className="mx-4">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">
                  Likely One-Time
                  <span className="ml-2 font-normal text-muted-foreground">({data.one_time_overspends.length})</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-0">
                <p className="px-6 pb-2 text-xs text-muted-foreground">
                  Cover from Holding: Next Month. Statistically unusual spend vs history.
                </p>
                <CoveragePlanTable categories={data.one_time_overspends} />
              </CardContent>
            </Card>
          )}

          {/* Structural overspends */}
          {data.overspent_categories.length > 0 && (
            <Card className="mx-4">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">
                  {data.one_time_overspends.length > 0 ? "Structural Overspends" : "Overspent Categories"}
                  <span className="ml-2 font-normal text-muted-foreground">({data.overspent_categories.length})</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-0">
                <CoveragePlanTable categories={data.overspent_categories} />
              </CardContent>
            </Card>
          )}

          {/* Classification legend */}
          {Object.keys(data.classification_summary).length > 0 && (
            <div className="mx-4 rounded-lg border bg-background px-4 py-3">
              <p className="text-xs font-medium mb-2">By Classification</p>
              <div className="flex flex-wrap gap-3">
                {(["STRUCTURAL", "SEASONAL", "ONE-OFF", "TIMING FLOAT"] as const).map((cls) => {
                  const entry = data.classification_summary[cls]
                  if (!entry) return null
                  return (
                    <div key={cls} className="flex items-center gap-2">
                      <ClassificationBadge label={cls} />
                      <span className="text-sm text-muted-foreground">
                        {entry.count} ({formatCurrency(entry.total)})
                      </span>
                    </div>
                  )
                })}
              </div>
              <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
                <strong>STRUCTURAL</strong>: 3+ of 6 months over.{" "}
                <strong>SEASONAL</strong>: same month last year was also over.{" "}
                <strong>ONE-OFF</strong>: single large transaction.{" "}
                <strong>TIMING FLOAT</strong>: reimbursement expected.
              </p>
            </div>
          )}

          {/* Donor waterfall */}
          <Card className="mx-4">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium">
                Coverage Waterfall
                <span className="ml-2 font-normal text-muted-foreground">(suggested donors)</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="px-0">
              <DonorTable donors={data.donors} />
              {data.donors.length > 0 && (
                <div className="px-6 pt-2 pb-1 flex flex-col gap-1">
                  <p className="text-sm font-medium">
                    Total suggested:{" "}
                    <span
                      className={cn(
                        "tabular-nums",
                        data.fully_coverable
                          ? "text-green-600 dark:text-green-400"
                          : "text-amber-600 dark:text-amber-400",
                      )}
                    >
                      {formatCurrency(data.total_coverable)}
                    </span>
                    {data.fully_coverable ? (
                      <span className="ml-2 text-xs text-green-600 dark:text-green-400">fully covers overspend</span>
                    ) : (
                      <span className="ml-2 text-xs text-muted-foreground">
                        ({formatCurrency(data.gap)} gap)
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Move money from these categories in YNAB to cover the overspend before month-end.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}

export function MonthEnd() {
  const { data, isLoading, error } = useMonthEnd()

  if (isLoading || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-xl font-semibold">Month-End Closeout</h2>
        <p className="text-muted-foreground text-sm">Loading...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="space-y-6">
        <h2 className="text-xl font-semibold">Month-End Closeout</h2>
        <p className="text-sm text-red-600 dark:text-red-400">
          Failed to load data. Run <code className="font-mono">ynab sync</code> first.
        </p>
      </div>
    )
  }

  const { spending, overspent, attention, credit_cards, planned_expenses } = data

  const structuralOverspends = overspent.categories.filter(
    (c: MonthEndOverspentCategory) => c.coverage_suggestion !== "holding",
  )
  const oneTimeOverspends = overspent.categories.filter(
    (c: MonthEndOverspentCategory) => c.coverage_suggestion === "holding",
  )

  const rtaVariant = spending.rta > 0 ? "positive" : spending.rta < 0 ? "negative" : "neutral"
  const overspentVariant = overspent.count === 0 ? "positive" : "negative"
  const attentionTotal = attention.uncategorized + attention.unapproved
  const attentionVariant = attentionTotal === 0 ? "positive" : "negative"

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Month-End Closeout</h2>
        <p className="text-sm text-muted-foreground mt-0.5">{formatMonth(data.month)}</p>
      </div>

      {/* KPI summary row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KPICard
          label="Ready to Assign"
          value={formatCurrency(spending.rta)}
          variant={rtaVariant}
          subtitle={spending.rta === 0 ? "fully assigned" : undefined}
        />
        <KPICard
          label="Overspent"
          value={
            overspent.count === 0
              ? "None"
              : `${overspent.count} (${formatCurrency(overspent.total_overspent)})`
          }
          variant={overspentVariant}
          subtitle={overspent.count === 0 ? "all clear" : undefined}
        />
        <KPICard
          label="CC Balance Total"
          value={formatCurrency(Math.abs(data.credit_card_total_balance))}
          variant={data.credit_card_total_balance < 0 ? "negative" : "neutral"}
        />
        <KPICard
          label="Needs Attention"
          value={String(attentionTotal)}
          variant={attentionVariant}
          subtitle={attentionTotal === 0 ? "inbox clear" : `${attention.unapproved} unapproved, ${attention.uncategorized} uncategorized`}
        />
      </div>

      {/* Overspent categories */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            {oneTimeOverspends.length > 0 ? "Structural Overspends" : "Overspent Categories"}
            {overspent.count > 0 && structuralOverspends.length > 0 && (
              <span className="ml-2 font-normal text-muted-foreground">({structuralOverspends.length})</span>
            )}
            {overspent.count > 0 && structuralOverspends.length === 0 && (
              <span className="ml-2 font-normal text-muted-foreground">({overspent.count})</span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          {overspent.count === 0 ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">
              No overspent categories. All balances are positive or zero.
            </p>
          ) : structuralOverspends.length === 0 ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">
              No structural overspends. All overspend appears to be one-time.
            </p>
          ) : (
            <OverspentTable categories={structuralOverspends} />
          )}
        </CardContent>
      </Card>

      {/* Likely one-time overspends */}
      {oneTimeOverspends.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">
              Likely One-Time
              <span className="ml-2 font-normal text-muted-foreground">({oneTimeOverspends.length})</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="px-0">
            <p className="px-6 pb-2 text-xs text-muted-foreground">
              Cover from Holding, no budget action needed. These categories had unusually high spend vs their history.
            </p>
            <OverspentTable categories={oneTimeOverspends} dimmed />
          </CardContent>
        </Card>
      )}

      {/* Credit card balances */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            Credit Card Balances
            {credit_cards.length > 0 && (
              <span className="ml-2 font-normal text-muted-foreground">({credit_cards.length})</span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          {credit_cards.length === 0 ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">No open credit card accounts.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead className="text-right">Balance</TableHead>
                  <TableHead className="text-right">Payment Budgeted</TableHead>
                  <TableHead className="text-right hidden sm:table-cell">Payment Balance</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {credit_cards.map((cc) => {
                  const balanceNegative = cc.balance < 0
                  const owed = cc.balance < 0 ? Math.abs(cc.balance) : 0
                  const gap = owed - cc.payment_balance
                  const underfunded = gap > 0.01
                  return (
                    <TableRow key={cc.name}>
                      <TableCell className="font-medium">{cc.name}</TableCell>
                      <TableCell
                        className={cn(
                          "text-right tabular-nums text-sm font-medium",
                          balanceNegative
                            ? "text-red-600 dark:text-red-400"
                            : "text-green-600 dark:text-green-400",
                        )}
                      >
                        {formatCurrency(cc.balance)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-sm">
                        {formatCurrency(cc.payment_budgeted)}
                      </TableCell>
                      <TableCell
                        className={cn(
                          "text-right tabular-nums text-sm hidden sm:table-cell",
                          underfunded ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground",
                        )}
                      >
                        {formatCurrency(cc.payment_balance)}
                        {underfunded && (
                          <span className="ml-1 text-xs">(gap {formatCurrency(gap)})</span>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Planned expenses */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">
            Planned Expenses This Month
            {planned_expenses.length > 0 && (
              <span className="ml-2 font-normal text-muted-foreground">
                ({planned_expenses.length})
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          {planned_expenses.length === 0 ? (
            <p className="px-6 py-4 text-sm text-muted-foreground">
              No planned expenses due this month.
            </p>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Category</TableHead>
                    <TableHead>Due</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead className="text-right">Status</TableHead>
                    <TableHead className="hidden md:table-cell">Memo</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {planned_expenses.map((plan) => (
                    <TableRow key={plan.id}>
                      <TableCell className="font-medium">{plan.category_name}</TableCell>
                      <TableCell
                        className={cn(
                          "text-sm whitespace-nowrap",
                          plan.overdue
                            ? "text-red-600 dark:text-red-400 font-medium"
                            : "text-muted-foreground",
                        )}
                      >
                        {formatDate(plan.due_date)}
                        {plan.overdue && <span className="ml-1 text-xs">(overdue)</span>}
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-sm">
                        {formatCurrency(plan.amount)}
                      </TableCell>
                      <TableCell className="text-right">
                        {plan.funded ? (
                          <span className="text-xs font-medium text-green-600 dark:text-green-400">
                            Funded
                          </span>
                        ) : (
                          <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
                            Gap {formatCurrency(plan.gap)}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="hidden md:table-cell text-xs text-muted-foreground max-w-[180px] truncate">
                        {plan.memo ?? ""}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {data.planned_total_gap > 0 && (
                <p className="px-6 pt-2 pb-3 text-sm font-medium text-amber-600 dark:text-amber-400">
                  Total funding gap: {formatCurrency(data.planned_total_gap)}
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Overspend coverage plan: collapsible, only shown when overspends exist */}
      <OverspendCoverageSection />
    </div>
  )
}
