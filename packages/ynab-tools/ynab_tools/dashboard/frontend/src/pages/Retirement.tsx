import { AlertTriangle, Info } from "lucide-react"
import {
  AreaChart,
  Area,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { KPICard } from "@/components/KPICard"
import { useRetirement, formatCurrency } from "@/lib/api"
import type { RetirementContributions, SocialSecurityData } from "@/lib/api"

const ACCOUNT_LABELS: Record<string, string> = {
  "401k": "401(k)",
  "roth_ira": "Roth IRA",
  "trad_ira": "Traditional IRA",
  "taxable": "Taxable Brokerage",
}

const CONTRIB_ROW_LABELS: Record<string, string> = {
  contribution: "Employee",
  match: "Employer match",
  conversion: "Roth conversion",
  dividend: "Dividends",
  fee: "Fees",
}

const STATUS_STYLES: Record<string, string> = {
  ahead: "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20 text-green-800 dark:text-green-300",
  on_track: "border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/20 text-blue-800 dark:text-blue-300",
  behind: "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/20 text-red-800 dark:text-red-300",
}

const STATUS_LABEL: Record<string, string> = {
  ahead: "Ahead of target",
  on_track: "On track",
  behind: "Behind target",
}

const PROJ_COLOR = "oklch(0.6 0.18 220)"
const MILESTONE_COLOR = "oklch(0.5 0.12 270)"

function ProgressBar({ pct, variant = "default" }: { pct: number; variant?: "default" | "warning" | "success" }) {
  const clamp = Math.min(100, Math.max(0, pct))
  const bg = variant === "success" ? "bg-green-500" : variant === "warning" ? "bg-amber-500" : "bg-blue-500"
  return (
    <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
      <div className={`h-full rounded-full transition-all ${bg}`} style={{ width: `${clamp}%` }} />
    </div>
  )
}

function ContributionsCard({ contributions }: { contributions: RetirementContributions }) {
  const { year, catch_up_eligible, limits, by_account, annual_pace, employer_match_annual } = contributions

  const iraUnfunded = limits.ira.effective_limit > 0 && limits.ira.contributed === 0

  const limitRows = [
    {
      label: "Employee 401(k)",
      sublabel: catch_up_eligible
        ? `$${limits.employee_401k.limit.toLocaleString()} + $${limits.employee_401k.catch_up.toLocaleString()} catch-up`
        : `limit $${limits.employee_401k.effective_limit.toLocaleString()}`,
      contributed: limits.employee_401k.contributed,
      effective_limit: limits.employee_401k.effective_limit,
      pct: limits.employee_401k.pct,
    },
    {
      label: "Total 401(k)",
      sublabel: `limit $${limits.total_401k.limit.toLocaleString()} (incl. match + mega backdoor)`,
      contributed: limits.total_401k.contributed,
      effective_limit: limits.total_401k.limit,
      pct: limits.total_401k.pct,
    },
    {
      label: "IRA",
      sublabel: catch_up_eligible
        ? `$${limits.ira.limit.toLocaleString()} + $${limits.ira.catch_up.toLocaleString()} catch-up`
        : `limit $${limits.ira.effective_limit.toLocaleString()}`,
      contributed: limits.ira.contributed,
      effective_limit: limits.ira.effective_limit,
      pct: limits.ira.pct,
    },
  ].filter((r) => r.effective_limit > 0 || r.contributed !== 0)

  const accountKeys = Object.keys(by_account)
  const contribCategories = ["contribution", "match", "conversion", "dividend", "fee"]
  const activeCategories = contribCategories.filter((cat) =>
    accountKeys.some((k) => Math.abs(by_account[k]?.[cat] ?? 0) > 0)
  )

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Contributions ({year})</CardTitle>
          <span className="text-xs text-muted-foreground">
            Annual pace: <span className="font-medium text-foreground">{formatCurrency(annual_pace)}</span>
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-3">
          {limitRows.map((row) => (
            <div key={row.label} className="space-y-1">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium">{row.label}</span>
                <span className="tabular-nums text-muted-foreground text-xs">
                  {formatCurrency(row.contributed)} / {formatCurrency(row.effective_limit)} ({row.pct}%)
                </span>
              </div>
              <ProgressBar
                pct={row.pct}
                variant={row.pct >= 100 ? "success" : row.pct >= 60 ? "default" : "warning"}
              />
              <p className="text-xs text-muted-foreground">{row.sublabel}</p>
            </div>
          ))}
        </div>

        {iraUnfunded && (
          <div className="rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>
              No {year} IRA contributions yet.{" "}
              {formatCurrency(limits.ira.effective_limit)} available (deadline: April {year + 1}).
            </span>
          </div>
        )}

        {activeCategories.length > 0 && (
          <div className="pt-2 border-t">
            <p className="text-xs text-muted-foreground mb-2">Breakdown by account</p>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground border-b">
                  <th className="text-left pb-1.5 font-medium">Type</th>
                  {accountKeys.map((k) => (
                    <th key={k} className="text-right pb-1.5 font-medium">
                      {ACCOUNT_LABELS[k] ?? k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {activeCategories.map((cat) => (
                  <tr key={cat} className="border-b last:border-0">
                    <td className="py-1.5 text-muted-foreground">{CONTRIB_ROW_LABELS[cat] ?? cat}</td>
                    {accountKeys.map((k) => {
                      const v = by_account[k]?.[cat] ?? 0
                      return (
                        <td
                          key={k}
                          className={`py-1.5 text-right tabular-nums ${v < 0 ? "text-red-600 dark:text-red-400" : ""}`}
                        >
                          {v !== 0 ? formatCurrency(v) : "-"}
                        </td>
                      )
                    })}
                  </tr>
                ))}
                {employer_match_annual > 0 && (
                  <tr className="border-t">
                    <td className="py-1.5 text-muted-foreground">Employer match (est.)</td>
                    {accountKeys.map((k, i) => (
                      <td key={k} className="py-1.5 text-right tabular-nums text-muted-foreground">
                        {i === 0 ? formatCurrency(employer_match_annual) : "-"}
                      </td>
                    ))}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function SocialSecurityCard({ ss }: { ss: SocialSecurityData }) {
  if (!ss.configured) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Social Security</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 flex items-start gap-2">
            <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <span>
              Social Security benefits not configured. Add{" "}
              <code>YNAB_SS_FRA_BENEFIT</code>, <code>YNAB_SS_FRA_AGE</code>, and related keys to your config to see combined retirement income projections.
              Get your current estimates at{" "}
              <a href="https://ssa.gov/myaccount" target="_blank" rel="noreferrer" className="underline">
                ssa.gov/myaccount
              </a>
              .
            </span>
          </div>
        </CardContent>
      </Card>
    )
  }

  const { scenarios, combined, statement_date } = ss

  // Check if statement is more than 12 months old
  const isStale = (() => {
    if (!statement_date) return false
    const d = new Date(statement_date)
    const cutoff = new Date()
    cutoff.setFullYear(cutoff.getFullYear() - 1)
    return d < cutoff
  })()

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Social Security</CardTitle>
          {statement_date && (
            <span className={`text-xs ${isStale ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground"}`}>
              Statement: {statement_date}{isStale ? " (update at ssa.gov)" : ""}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Claim age scenarios */}
        {scenarios.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-2">Monthly benefit by claim age</p>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground border-b">
                  <th className="text-left pb-1.5 font-medium">Claim Age</th>
                  <th className="text-right pb-1.5 font-medium">Monthly Benefit</th>
                  <th className="text-right pb-1.5 font-medium">Annual Benefit</th>
                </tr>
              </thead>
              <tbody>
                {scenarios.map((s) => (
                  <tr key={s.label} className="border-b last:border-0">
                    <td className="py-1.5">
                      <span className="font-medium">{s.label}</span>
                      <span className="text-muted-foreground ml-2 text-xs">(age {s.age})</span>
                    </td>
                    <td className="py-1.5 text-right tabular-nums">{formatCurrency(s.monthly_benefit)}</td>
                    <td className="py-1.5 text-right tabular-nums text-muted-foreground">{formatCurrency(s.monthly_benefit * 12)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Combined income projections */}
        {combined && (
          <div className="pt-2 border-t">
            <p className="text-xs text-muted-foreground mb-2">
              Combined retirement income projection (4% annual withdrawal from portfolio)
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground border-b">
                  <th className="text-left pb-1.5 font-medium">Scenario</th>
                  <th className="text-right pb-1.5 font-medium">Portfolio Draw</th>
                  <th className="text-right pb-1.5 font-medium">SS Benefit</th>
                  <th className="text-right pb-1.5 font-medium">Combined</th>
                </tr>
              </thead>
              <tbody>
                {combined.fra_total_monthly !== null && (
                  <tr className="border-b">
                    <td className="py-1.5 font-medium">At FRA</td>
                    <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                      {combined.fra_portfolio_monthly !== null ? formatCurrency(combined.fra_portfolio_monthly) : "-"}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                      {formatCurrency(combined.fra_ss_monthly)}
                    </td>
                    <td className="py-1.5 text-right tabular-nums font-semibold text-green-600 dark:text-green-400">
                      {formatCurrency(combined.fra_total_monthly)}
                    </td>
                  </tr>
                )}
                {combined.delayed_total_monthly !== null && combined.delayed_ss_monthly !== null && (
                  <tr className="border-b last:border-0">
                    <td className="py-1.5 font-medium">
                      {(() => {
                        const delayedScenario = scenarios.find((s) => s.label === "Delayed")
                        const delayedAge = delayedScenario?.age ?? 70
                        return `Delayed (age ${delayedAge})`
                      })()}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                      {combined.delayed_portfolio_monthly !== null ? formatCurrency(combined.delayed_portfolio_monthly) : "-"}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                      {formatCurrency(combined.delayed_ss_monthly)}
                    </td>
                    <td className="py-1.5 text-right tabular-nums font-semibold text-green-600 dark:text-green-400">
                      {formatCurrency(combined.delayed_total_monthly)}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            <p className="text-xs text-muted-foreground mt-2">
              Portfolio draw uses 4% withdrawal rule applied to projected balance at each claim age.
              Does not include taxes, Medicare premiums, or COLA adjustments.
            </p>
          </div>
        )}

        {!statement_date && (
          <p className="text-xs text-muted-foreground">
            Set <code>YNAB_SS_STATEMENT_DATE</code> to track statement freshness. Update annually at{" "}
            <a href="https://ssa.gov/myaccount" target="_blank" rel="noreferrer" className="underline">
              ssa.gov/myaccount
            </a>
            .
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function Retirement() {
  const { data, isLoading, error } = useRetirement()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading retirement data...
      </div>
    )
  }

  if (error || !data) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Failed to load retirement data</AlertTitle>
        <AlertDescription>
          Make sure the API server is running and data is synced (<code>ynab sync</code>).
        </AlertDescription>
      </Alert>
    )
  }

  if (!data.configured) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Retirement</h2>
        </div>
        <Card>
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm">
              Retirement accounts not configured.
            </p>
            <p className="text-muted-foreground text-sm mt-1">
              Set them up in <strong>Admin → Retirement</strong>.
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  const { current_age, accounts, total_invested, readiness, contributions, projection, return_rate_pct, social_security } = data
  const staleAccounts = accounts.filter((a) => a.stale)

  // Next checkpoint computation (refs #197)
  // Fidelity savings benchmarks: 1x@30, 2x@35, 3x@40, 4x@45, 6x@50, 7x@55, 8x@60, 10x@67
  const CHECKPOINTS = [
    { age: 30, mult: 1 },
    { age: 35, mult: 2 },
    { age: 40, mult: 3 },
    { age: 45, mult: 4 },
    { age: 50, mult: 6 },
    { age: 55, mult: 7 },
    { age: 60, mult: 8 },
    { age: 67, mult: 10 },
  ]
  const grossSalary = readiness?.gross_salary ?? null
  const nextCheckpoint = current_age != null && grossSalary
    ? CHECKPOINTS.find((c) => c.age > current_age!)
    : null
  let nextCheckpointLabel: string | null = null
  let nextCheckpointVariant: "positive" | "negative" | "default" = "default"
  if (nextCheckpoint && grossSalary && current_age != null) {
    const needed = grossSalary * nextCheckpoint.mult
    const projPoint = projection.find((p) => p.age === nextCheckpoint.age)
    const projBalance = projPoint?.balance ?? null
    if (projBalance !== null) {
      const delta = projBalance - needed
      const sign = delta >= 0 ? "+" : ""
      nextCheckpointLabel = `${sign}${new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(delta)} at age ${nextCheckpoint.age} (${nextCheckpoint.mult}x)`
      nextCheckpointVariant = delta >= 0 ? "positive" : "negative"
    } else {
      nextCheckpointLabel = `${nextCheckpoint.mult}x by age ${nextCheckpoint.age}`
    }
  }

  // Milestone reference lines from projection data
  const milestonePoints = projection.filter((p) => p.milestone && p.age != null)

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Retirement</h2>
        <p className="text-muted-foreground text-sm">Portfolio balance, readiness, and projection</p>
      </div>

      {staleAccounts.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>
            Stale data:{" "}
            {staleAccounts.map((a, i) => (
              <span key={a.type}>
                {i > 0 && ", "}
                <strong>{a.label}</strong>
                {a.days_stale != null && a.days_stale < 999 && ` (${a.days_stale}d)`}
              </span>
            ))}
            . Reconcile in YNAB to refresh.
          </span>
        </div>
      )}

      {/* KPI row */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KPICard
          label="Total Invested"
          value={formatCurrency(total_invested)}
          subtitle="across all accounts"
        />
        <KPICard
          label="Current Multiplier"
          value={readiness ? `${readiness.multiplier}x` : "-"}
          subtitle={
            readiness
              ? `target ${readiness.current_target_multiplier}x at ${current_age}`
              : "set gross salary in Admin"
          }
          variant={
            readiness
              ? readiness.status === "ahead"
                ? "positive"
                : readiness.status === "behind"
                  ? "negative"
                  : "default"
              : "neutral"
          }
        />
        <KPICard
          label="Annual Pace"
          value={contributions ? formatCurrency(contributions.annual_pace) : "-"}
          subtitle={
            contributions
              ? contributions.pace_basis_year !== contributions.year
                ? `based on ${contributions.pace_basis_year}`
                : `${contributions.year} projected total`
              : "no contribution data"
          }
        />
        <KPICard
          label="Next Checkpoint"
          value={nextCheckpoint ? `Age ${nextCheckpoint.age}` : "-"}
          subtitle={nextCheckpointLabel ?? (grossSalary ? "no upcoming checkpoint" : "set gross salary in Admin")}
          variant={nextCheckpoint ? nextCheckpointVariant : "neutral"}
          tooltip="Projected balance at the next Fidelity savings checkpoint vs the required salary multiple. Positive = on pace to exceed; negative = projected shortfall."
        />
      </div>

      {/* Account balances */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {accounts.map((acct) => (
          <KPICard
            key={acct.type}
            label={acct.label}
            value={formatCurrency(acct.balance)}
            subtitle={
              acct.stale
                ? `stale${acct.last_updated ? ` · ${acct.last_updated}` : ""}`
                : acct.last_updated ?? undefined
            }
            variant={acct.stale ? "negative" : "default"}
          />
        ))}
      </div>

      {/* Readiness */}
      {readiness && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Retirement Readiness</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className={`rounded-md border px-3 py-2 text-sm ${STATUS_STYLES[readiness.status]}`}>
              <span className="font-medium">{STATUS_LABEL[readiness.status]}:</span>{" "}
              At age {current_age}, the target is {readiness.current_target_multiplier}x salary (
              {formatCurrency(readiness.current_target_amount)}). You have {readiness.multiplier}x
              {readiness.gap >= 0
                ? `, ${formatCurrency(readiness.gap)} ahead`
                : `, ${formatCurrency(Math.abs(readiness.gap))} behind`}
              .
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground border-b">
                  <th className="text-left pb-1.5 font-medium">Age</th>
                  <th className="text-right pb-1.5 font-medium">Target</th>
                  <th className="text-right pb-1.5 font-medium">Target Amount</th>
                  <th className="text-right pb-1.5 font-medium">Projected</th>
                  <th className="text-right pb-1.5 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {readiness.targets.map((t, i) => {
                  const nextAge = readiness.targets[i + 1]?.age ?? Infinity
                  const isCurrent = current_age != null && t.age <= current_age && current_age < nextAge
                  const checkpointPassed = current_age != null && t.age <= current_age
                  const met = total_invested >= t.amount
                  const projPoint = projection.find((p) => p.age === t.age)
                  const projBalance = projPoint?.balance ?? null
                  const projMet = projBalance != null && projBalance >= t.amount
                  return (
                    <tr key={t.age} className={`border-b last:border-0 ${isCurrent ? "bg-muted/50" : ""}`}>
                      <td className="py-1.5 tabular-nums">
                        {t.age}
                        {isCurrent && <span className="ml-1.5 text-xs text-muted-foreground">(age {current_age})</span>}
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-muted-foreground">{t.multiplier}x</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCurrency(t.amount)}</td>
                      <td className={`py-1.5 text-right tabular-nums text-xs ${
                        checkpointPassed
                          ? "text-muted-foreground"
                          : projBalance == null
                            ? "text-muted-foreground"
                            : projMet
                              ? "text-green-600 dark:text-green-400"
                              : "text-amber-600 dark:text-amber-400"
                      }`}>
                        {checkpointPassed
                          ? formatCurrency(total_invested)
                          : projBalance != null
                            ? formatCurrency(projBalance)
                            : "-"}
                      </td>
                      <td
                        className={`py-1.5 text-right text-xs ${
                          met
                            ? "text-green-600 dark:text-green-400"
                            : checkpointPassed
                              ? "text-red-600 dark:text-red-400"
                              : projMet
                                ? "text-green-600 dark:text-green-400"
                                : "text-amber-600 dark:text-amber-400"
                        }`}
                      >
                        {met
                          ? "Met"
                          : checkpointPassed
                            ? "Missed"
                            : projMet
                              ? "On track"
                              : "Short"}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {/* Projection chart */}
      {projection.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Portfolio Projection</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground mb-3">
              Projected at {return_rate_pct}% nominal annual return, current contribution pace. Dashed lines mark configured milestones. Configurable via <code>YNAB_RETIREMENT_RATE</code> or Admin.
            </p>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={projection} margin={{ top: 20, right: 16, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="projGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={PROJ_COLOR} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={PROJ_COLOR} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis
                  dataKey="age"
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  label={{ value: "Age", position: "insideBottomRight", offset: -4, fontSize: 11 }}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: number) =>
                    v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(1)}M` : `$${(v / 1000).toFixed(0)}k`
                  }
                  width={56}
                />
                <Tooltip
                  formatter={(v) => [formatCurrency(v as number), "Balance"]}
                  labelFormatter={(_label, payload) => {
                    const item = payload?.[0]?.payload
                    if (!item) return ""
                    const parts = [`Age ${item.age} (${item.year})`]
                    if (item.milestone) parts.push(item.milestone)
                    return parts.join(" · ")
                  }}
                />
                {milestonePoints.map((p) => (
                  <ReferenceLine
                    key={p.year}
                    x={p.age ?? undefined}
                    stroke={MILESTONE_COLOR}
                    strokeDasharray="4 3"
                    label={{
                      value: String(p.age),
                      position: "top",
                      fontSize: 10,
                      fill: MILESTONE_COLOR,
                    }}
                  />
                ))}
                <Area
                  type="monotone"
                  dataKey="balance"
                  stroke={PROJ_COLOR}
                  strokeWidth={2}
                  fill="url(#projGradient)"
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Contributions */}
      {contributions && <ContributionsCard contributions={contributions} />}

      {/* Social Security */}
      <SocialSecurityCard ss={social_security ?? { configured: false, statement_date: null, scenarios: [], combined: null }} />
    </div>
  )
}
