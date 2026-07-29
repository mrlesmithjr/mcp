import { AlertTriangle } from "lucide-react"
import {
  ComposedChart,
  Bar,
  Line,
  CartesianGrid,
  XAxis,
  YAxis,
  Legend,
  Tooltip,
  BarChart,
  ResponsiveContainer,
} from "recharts"
import type { ChartConfig } from "@/components/ui/chart"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
} from "@/components/ui/chart"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { KPICard } from "@/components/KPICard"
import { useTrends, useGroupTrend, formatCurrency, formatMonth } from "@/lib/api"


const incomeSpendingConfig = {
  income: { label: "Income", color: "oklch(0.627 0.194 149.214)" },
  spending: { label: "Spending", color: "oklch(0.637 0.237 25.331)" },
  rolling_avg_spending: { label: "3-month avg spending", color: "oklch(0.5 0.2 270)" },
} satisfies ChartConfig

const GROUP_COLORS = [
  "oklch(0.627 0.194 149.214)",
  "oklch(0.637 0.237 25.331)",
  "oklch(0.6 0.18 220)",
  "oklch(0.65 0.2 60)",
  "oklch(0.55 0.22 300)",
  "oklch(0.62 0.18 35)",
]

export function Trends() {
  const { data: trends, isLoading: trendsLoading, error: trendsError } = useTrends(12)
  const { data: groupTrend, isLoading: groupLoading, error: groupError } = useGroupTrend(6)

  if (trendsLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading trend data...
      </div>
    )
  }

  if (trendsError || !trends) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Failed to load trend data</AlertTitle>
        <AlertDescription>
          Make sure the API server is running and data is synced (<code>ynab sync</code>).
        </AlertDescription>
      </Alert>
    )
  }

  // best_month and worst_month remain in the interface but are no longer rendered (refs #194)
  const { streak, avg_monthly_income, avg_monthly_spending, best_month: _best, worst_month: _worst, insight } = trends
  void _best; void _worst

  // Compute avg monthly surplus and trend direction from the month window
  const nets = trends.months.map((m) => m.net)
  const avgSurplus = nets.length > 0 ? nets.reduce((s, v) => s + v, 0) / nets.length : 0

  let trendDirection: "Improving" | "Declining" | "Stable" = "Stable"
  let trendVariant: "positive" | "negative" | "default" = "default"
  if (nets.length >= 6) {
    const last3 = nets.slice(-3)
    const prior3 = nets.slice(-6, -3)
    const last3Avg = last3.reduce((s, v) => s + v, 0) / 3
    const prior3Avg = prior3.reduce((s, v) => s + v, 0) / 3
    const diff = last3Avg - prior3Avg
    if (diff > 50) {
      trendDirection = "Improving"
      trendVariant = "positive"
    } else if (diff < -50) {
      trendDirection = "Declining"
      trendVariant = "negative"
    }
  }

  const incomeSpendingData = trends.months.map((m) => ({
    month: formatMonth(m.month),
    income: m.income,
    spending: m.spending,
    rolling_avg_spending: m.rolling_avg_spending,
  }))

  const groupChartData =
    groupTrend?.months.map((m, i) => ({
      month: formatMonth(m),
      ...Object.fromEntries(
        groupTrend.groups.slice(0, 6).map((g) => [g.group, g.totals[i] ?? 0])
      ),
    })) ?? []

  const topGroups = groupTrend?.groups.slice(0, 6) ?? []

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Trends</h2>
        <p className="text-muted-foreground text-sm">
          12-month income, spending, and category patterns
        </p>
      </div>

      {/* Insight callout */}
      {insight && (
        <div className={`rounded-lg border px-4 py-2.5 text-sm ${streak.type === "deficit" ? "border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/20 text-red-800 dark:text-red-300" : "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20 text-green-800 dark:text-green-300"}`}>
          {insight}
        </div>
      )}

      {/* KPI stat row */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <KPICard
          label="Streak"
          value={`${streak.count} month${streak.count === 1 ? "" : "s"}`}
          subtitle={streak.type === "surplus" ? "surplus streak" : "deficit streak"}
          variant={streak.type === "surplus" ? "positive" : "negative"}
        />
        <KPICard
          label="Avg Income"
          value={formatCurrency(avg_monthly_income)}
          subtitle="12-mo budget avg"
          variant="positive"
        />
        <KPICard
          label="Avg Spending"
          value={formatCurrency(avg_monthly_spending)}
          subtitle="12-month avg"
          variant="default"
        />
        <KPICard
          label="Avg Monthly Surplus"
          value={formatCurrency(avgSurplus)}
          subtitle="income minus spending"
          variant={avgSurplus >= 0 ? "positive" : "negative"}
        />
        <KPICard
          label="Trend Direction"
          value={trendDirection}
          subtitle={nets.length >= 6 ? "last 3 vs prior 3 months" : "insufficient data"}
          variant={trendVariant}
        />
      </div>

      {/* 12-month income vs spending chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Income vs Spending: Last 12 Months</CardTitle>
        </CardHeader>
        <CardContent>
          <ChartContainer config={incomeSpendingConfig} className="min-h-[260px] w-full">
            <ComposedChart data={incomeSpendingData} barGap={4}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="month"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12 }}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12 }}
                tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
                width={48}
              />
              <ChartTooltip content={<ChartTooltipContent />} />
              <ChartLegend content={<ChartLegendContent />} />
              <Bar dataKey="income" fill="var(--color-income)" radius={[4, 4, 0, 0]} />
              <Bar dataKey="spending" fill="var(--color-spending)" radius={[4, 4, 0, 0]} />
              <Line
                dataKey="rolling_avg_spending"
                type="monotone"
                stroke="var(--color-rolling_avg_spending)"
                strokeWidth={2}
                dot={false}
                strokeDasharray="4 4"
              />
            </ComposedChart>
          </ChartContainer>
        </CardContent>
      </Card>

      {/* Category group spending chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Spending by Category Group (Last 6 Months)</CardTitle>
        </CardHeader>
        <CardContent>
          {groupLoading || !groupTrend ? (
            <div className="h-[220px] flex items-center justify-center text-muted-foreground text-sm">
              Loading chart...
            </div>
          ) : groupError ? (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Failed to load group data</AlertTitle>
              <AlertDescription>Could not load category group spending.</AlertDescription>
            </Alert>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={groupChartData} barGap={4}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis
                  dataKey="month"
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 12 }}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 12 }}
                  tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
                  width={48}
                />
                <Tooltip
                  formatter={(value) => formatCurrency(value as number)}
                />
                <Legend />
                {topGroups.map((g, i) => (
                  <Bar
                    key={g.group}
                    dataKey={g.group}
                    stackId="a"
                    fill={GROUP_COLORS[i] ?? GROUP_COLORS[0]}
                    radius={i === topGroups.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
