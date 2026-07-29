import { useState } from "react"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis, ReferenceLine } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { KPICard } from "@/components/KPICard"
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import type { ChartConfig } from "@/components/ui/chart"
import { useIncome, formatCurrency, formatMonth } from "@/lib/api"
import type { IncomeMonth } from "@/lib/api"
import { cn } from "@/lib/utils"

const chartConfig = {
  regular: { label: "Regular Pay", color: "oklch(0.6 0.18 142)" },
  bonus: { label: "Bonus", color: "oklch(0.7 0.18 60)" },
  other: { label: "Other", color: "oklch(0.6 0.1 250)" },
} satisfies ChartConfig

function MonthRow({ month, hasBonusConfig }: { month: IncomeMonth; hasBonusConfig: boolean }) {
  const [open, setOpen] = useState(false)
  const hasBonus = month.bonus > 0
  return (
    <div className={cn("border-b last:border-b-0", hasBonus && "bg-amber-50/50 dark:bg-amber-900/10")}>
      <div
        className="flex items-center justify-between py-2.5 px-1 cursor-pointer hover:bg-muted/40 transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium w-16">{formatMonth(month.month)}</span>
          {hasBonus && (
            <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
              Bonus
            </span>
          )}
        </div>
        <div className="flex items-center gap-6 text-sm">
          <span className="text-muted-foreground">{formatCurrency(month.regular)} regular</span>
          {hasBonusConfig && month.bonus > 0 && (
            <span className="text-amber-700 dark:text-amber-400 font-medium">+{formatCurrency(month.bonus)} bonus</span>
          )}
          {month.other > 0 && <span className="text-muted-foreground">{formatCurrency(month.other)} other</span>}
          <span className="font-semibold w-20 text-right">{formatCurrency(month.total)}</span>
        </div>
      </div>
      {open && month.paychecks.length > 0 && (
        <div className="pb-3 px-4 space-y-1">
          {month.paychecks.map((p, i) => (
            <div key={i} className="flex items-center justify-between text-xs text-muted-foreground py-1">
              <span className="flex items-center gap-2">
                <span>{p.date}</span>
                <span>{p.payee}</span>
                {p.is_bonus && (
                  <span className="inline-flex items-center rounded-full px-1.5 py-0.5 text-xs bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                    bonus
                  </span>
                )}
              </span>
              <span className="font-medium">{formatCurrency(p.amount)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function Income() {
  const [months, setMonths] = useState(12)
  const { data, isLoading, error } = useIncome(months)

  if (isLoading) return <div className="text-sm text-muted-foreground">Loading...</div>
  if (error || !data) return <div className="text-sm text-destructive">Failed to load income data.</div>

  const chartData = [...data.months]
    .reverse()
    .map((m) => ({
      month: formatMonth(m.month),
      regular: m.regular,
      bonus: m.bonus,
      other: m.other,
    }))

  // YTD vs Expected Pace: use 12-month net average as baseline so comparison is
  // net vs. net (#213). Fall back to gross-derived target only if no history exists.
  const monthsElapsed = new Date().getMonth() + 1
  const monthlyBase = data.avg_monthly_net ?? (data.gross_ote ?? data.gross_salary ? (data.gross_ote ?? data.gross_salary)! / 12 : null)
  const expectedYtd = monthlyBase !== null ? monthlyBase * monthsElapsed : null
  const ytdPaceDelta = expectedYtd !== null ? data.ytd_total - expectedYtd : null
  const usingHistoricalAvg = data.avg_monthly_net !== null

  const ytdSalaryPct =
    data.gross_salary && data.gross_salary > 0
      ? Math.min((data.ytd_regular / data.gross_salary) * 100, 100)
      : null

  const bonusTarget =
    data.gross_ote && data.gross_salary && data.gross_ote > data.gross_salary
      ? data.gross_ote - data.gross_salary
      : null
  const ytdBonusPct =
    bonusTarget && bonusTarget > 0
      ? Math.min((data.ytd_bonus / bonusTarget) * 100, 100)
      : null

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Income</h2>
          <p className="text-sm text-muted-foreground">Regular pay, bonuses, and YTD summary for {data.current_year}</p>
        </div>
        <select
          value={months}
          onChange={(e) => setMonths(Number(e.target.value))}
          className="text-sm border rounded-md px-2 py-1 bg-background"
        >
          <option value={6}>6 months</option>
          <option value={12}>12 months</option>
          <option value={24}>24 months</option>
        </select>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <KPICard
          label={`YTD Pace (${data.current_year})`}
          value={
            ytdPaceDelta !== null
              ? `${ytdPaceDelta >= 0 ? "+" : ""}${formatCurrency(ytdPaceDelta)} ${ytdPaceDelta >= 0 ? "ahead" : "behind"}`
              : formatCurrency(data.ytd_total)
          }
          subtitle={
            expectedYtd !== null
              ? usingHistoricalAvg
                ? `vs ${formatCurrency(expectedYtd)} expected (${data.avg_monthly_net_months}-mo avg)`
                : `vs ${formatCurrency(expectedYtd)} expected (gross target)`
              : `month ${monthsElapsed} of 12`
          }
          variant={
            ytdPaceDelta === null
              ? "default"
              : Math.abs(ytdPaceDelta) / (expectedYtd ?? 1) < 0.05
                ? "default"
                : ytdPaceDelta >= 0
                  ? "positive"
                  : "negative"
          }
          tooltip="Expected pace = 12-month net average × months elapsed. Compares total YTD income against your own historical baseline (net vs. net)."
        />
        <KPICard label="YTD Regular Pay" value={formatCurrency(data.ytd_regular)} variant="default" />
        <KPICard
          label="YTD Bonus"
          value={formatCurrency(data.ytd_bonus)}
          variant={data.ytd_bonus > 0 ? "positive" : "neutral"}
        />
        <KPICard label="YTD Other" value={formatCurrency(data.ytd_other)} variant="default" />
      </div>

      {ytdSalaryPct !== null && data.gross_salary && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">YTD Regular Pay vs Base Salary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>{formatCurrency(data.ytd_regular)} regular pay received</span>
              <span className="text-muted-foreground">{formatCurrency(data.gross_salary)} base salary target</span>
            </div>
            <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-emerald-500 transition-all"
                style={{ width: `${ytdSalaryPct}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">{ytdSalaryPct.toFixed(1)}% of base salary received</p>
          </CardContent>
        </Card>
      )}

      {ytdBonusPct !== null && bonusTarget && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">YTD Bonus vs Target Bonus</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>{formatCurrency(data.ytd_bonus)} bonus received</span>
              <span className="text-muted-foreground">{formatCurrency(bonusTarget)} target (On-Target Earnings minus base pay)</span>
            </div>
            <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-amber-500 transition-all"
                style={{ width: `${ytdBonusPct}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">{ytdBonusPct.toFixed(1)}% of target bonus received</p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Monthly Income Breakdown</CardTitle>
        </CardHeader>
        <CardContent>
          <ChartContainer config={chartConfig} className="h-52 w-full">
            <BarChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis
                tick={{ fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) => formatCurrency(v, true)}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    formatter={(value) => formatCurrency(value as number)}
                  />
                }
              />
              <Bar dataKey="regular" stackId="a" fill="var(--color-regular)" radius={[0, 0, 2, 2]} />
              {data.has_bonus_config && (
                <Bar dataKey="bonus" stackId="a" fill="var(--color-bonus)" radius={[2, 2, 0, 0]} />
              )}
              <Bar dataKey="other" stackId="a" fill="var(--color-other)" />
              {data.gross_salary && (
                <ReferenceLine
                  y={data.gross_salary / 12}
                  stroke="oklch(0.6 0 0)"
                  strokeDasharray="4 4"
                  label={{ value: "Monthly target", position: "insideTopRight", fontSize: 10 }}
                />
              )}
            </BarChart>
          </ChartContainer>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Month-by-Month Detail</CardTitle>
        </CardHeader>
        <CardContent>
          {data.months.length === 0 ? (
            <p className="text-sm text-muted-foreground">No income data in the selected period.</p>
          ) : (
            <div>
              {data.months.map((m) => (
                <MonthRow key={m.month} month={m} hasBonusConfig={data.has_bonus_config} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
