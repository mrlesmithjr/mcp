import { AlertTriangle, TrendingUp, TrendingDown } from "lucide-react"
import {
  AreaChart,
  Area,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { KPICard } from "@/components/KPICard"
import { useNetWorth, formatCurrency, formatMonth } from "@/lib/api"
import type { NetWorthAccountDetail } from "@/lib/api"

const NET_WORTH_COLOR = "oklch(0.6 0.18 220)"
const EX_MORTGAGE_COLOR = "oklch(0.6 0.15 150)"

function DeltaBadge({ value, fromMonth }: { value: number | null; fromMonth: string | null }) {
  if (value === null) return <span className="text-muted-foreground text-xs">-</span>
  const positive = value >= 0
  const Icon = positive ? TrendingUp : TrendingDown
  const color = positive
    ? "text-green-600 dark:text-green-400"
    : "text-red-600 dark:text-red-400"
  const sign = positive ? "+" : ""
  return (
    <span className={`inline-flex items-center gap-1 text-sm font-medium ${color}`}>
      <Icon className="h-3.5 w-3.5" />
      {sign}{formatCurrency(value)}
      {fromMonth && <span className="text-muted-foreground font-normal text-xs">vs {formatMonth(fromMonth + "-01")}</span>}
    </span>
  )
}

function AccountsTable({ accounts }: { accounts: NetWorthAccountDetail[] }) {
  const assets = accounts.filter((a) => a.balance >= 0).sort((a, b) => b.balance - a.balance)
  const debts = accounts.filter((a) => a.balance < 0).sort((a, b) => a.balance - b.balance)

  function Row({ acct }: { acct: NetWorthAccountDetail }) {
    const isDebt = acct.balance < 0
    return (
      <tr className="border-b last:border-0">
        <td className="py-1.5">
          <span className="font-medium text-sm">{acct.name}</span>
          <span className="ml-2 text-xs text-muted-foreground">{acct.type}</span>
          {!acct.on_budget && (
            <span className="ml-1 text-xs text-muted-foreground">(off-budget)</span>
          )}
        </td>
        <td className={`py-1.5 text-right tabular-nums text-sm ${isDebt ? "text-red-600 dark:text-red-400" : ""}`}>
          {formatCurrency(acct.balance)}
        </td>
      </tr>
    )
  }

  return (
    <div className="space-y-4">
      {assets.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">Assets</p>
          <table className="w-full">
            <tbody>
              {assets.map((a) => <Row key={a.name} acct={a} />)}
            </tbody>
          </table>
        </div>
      )}
      {debts.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">Liabilities</p>
          <table className="w-full">
            <tbody>
              {debts.map((a) => <Row key={a.name} acct={a} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function NetWorth() {
  const { data, isLoading, error } = useNetWorth()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading net worth data...
      </div>
    )
  }

  if (error || !data) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Failed to load net worth data</AlertTitle>
        <AlertDescription>
          Make sure the API server is running and data is synced (<code>ynab sync</code>).
        </AlertDescription>
      </Alert>
    )
  }

  if (!data.has_data || !data.current) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Net Worth</h2>
        </div>
        <Card>
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm">No net worth snapshots found.</p>
            <p className="text-muted-foreground text-sm mt-1">
              Run <code>ynab net-worth</code> or enable auto-sync to start tracking.
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  const { current, history, mom_change, mom_from_month, yoy_change, yoy_from_month, breakdown } = data

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Net Worth</h2>
        <p className="text-muted-foreground text-sm">
          Total wealth snapshot · as of {data.as_of}
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KPICard
          label="Net Worth"
          value={formatCurrency(current.net_worth)}
          subtitle="total including mortgage"
          variant={current.net_worth >= 0 ? "positive" : "negative"}
        />
        <KPICard
          label="Ex-Mortgage"
          value={formatCurrency(current.net_worth_ex_mortgage)}
          subtitle="excl. mortgage liability"
          variant={current.net_worth_ex_mortgage >= 0 ? "positive" : "negative"}
        />
        <KPICard
          label="MoM Change"
          value={mom_change !== null ? `${mom_change >= 0 ? "+" : ""}${formatCurrency(mom_change)}` : "-"}
          subtitle={mom_from_month ? `vs ${formatMonth(mom_from_month + "-01")}` : "no prior month"}
          variant={mom_change === null ? "neutral" : mom_change >= 0 ? "positive" : "negative"}
        />
        <KPICard
          label="YoY Change"
          value={yoy_change !== null ? `${yoy_change >= 0 ? "+" : ""}${formatCurrency(yoy_change)}` : "-"}
          subtitle={yoy_from_month ? `vs ${formatMonth(yoy_from_month + "-01")}` : "less than 12mo history"}
          variant={yoy_change === null ? "neutral" : yoy_change >= 0 ? "positive" : "negative"}
        />
      </div>

      {/* Delta summary row */}
      {(mom_change !== null || yoy_change !== null) && (
        <div className="flex flex-wrap gap-6 text-sm px-1">
          {mom_change !== null && (
            <span>
              <span className="text-muted-foreground mr-1">Month over month:</span>
              <DeltaBadge value={mom_change} fromMonth={mom_from_month} />
            </span>
          )}
          {yoy_change !== null && (
            <span>
              <span className="text-muted-foreground mr-1">Year over year:</span>
              <DeltaBadge value={yoy_change} fromMonth={yoy_from_month} />
            </span>
          )}
        </div>
      )}

      {/* Trend chart */}
      {history.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Net Worth Trend</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground mb-3">
              Up to 24 months of monthly snapshots. Ex-mortgage removes the mortgage liability to show investment trajectory.
            </p>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={history} margin={{ top: 10, right: 16, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="nwGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={NET_WORTH_COLOR} stopOpacity={0.2} />
                    <stop offset="95%" stopColor={NET_WORTH_COLOR} stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="exMortgageGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={EX_MORTGAGE_COLOR} stopOpacity={0.2} />
                    <stop offset="95%" stopColor={EX_MORTGAGE_COLOR} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis
                  dataKey="month"
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: string) => formatMonth(v + "-01")}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v: number) =>
                    v >= 1_000_000
                      ? `$${(v / 1_000_000).toFixed(1)}M`
                      : v <= -1_000_000
                        ? `-$${(Math.abs(v) / 1_000_000).toFixed(1)}M`
                        : `$${(v / 1000).toFixed(0)}k`
                  }
                  width={60}
                />
                <Tooltip
                  formatter={(v, name) => [
                    formatCurrency(v as number),
                    name === "net_worth" ? "Net Worth" : "Ex-Mortgage",
                  ]}
                  labelFormatter={(label) => formatMonth(String(label) + "-01")}
                />
                <Legend
                  formatter={(value) => (value === "net_worth" ? "Net Worth" : "Ex-Mortgage")}
                />
                <Area
                  type="monotone"
                  dataKey="net_worth"
                  stroke={NET_WORTH_COLOR}
                  strokeWidth={2}
                  fill="url(#nwGradient)"
                  dot={false}
                />
                <Area
                  type="monotone"
                  dataKey="net_worth_ex_mortgage"
                  stroke={EX_MORTGAGE_COLOR}
                  strokeWidth={2}
                  fill="url(#exMortgageGradient)"
                  dot={false}
                  strokeDasharray="4 2"
                />
              </AreaChart>
            </ResponsiveContainer>
            <p className="text-xs text-muted-foreground mt-2">
              Ex-mortgage line in history is approximate - based on off-budget debt balances. Current month breakdown is exact.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Breakdown */}
      {breakdown && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {/* Summary buckets */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Asset &amp; Liability Breakdown</CardTitle>
            </CardHeader>
            <CardContent>
              <table className="w-full text-sm">
                <tbody>
                  <tr className="border-b">
                    <td className="py-1.5 text-muted-foreground">Investments (off-budget)</td>
                    <td className="py-1.5 text-right tabular-nums font-medium text-green-600 dark:text-green-400">
                      {formatCurrency(breakdown.investments)}
                    </td>
                  </tr>
                  <tr className="border-b">
                    <td className="py-1.5 text-muted-foreground">Liquid (on-budget assets)</td>
                    <td className="py-1.5 text-right tabular-nums font-medium">
                      {formatCurrency(breakdown.liquid)}
                    </td>
                  </tr>
                  <tr className="border-b">
                    <td className="py-1.5 text-muted-foreground">Mortgage</td>
                    <td className="py-1.5 text-right tabular-nums font-medium text-red-600 dark:text-red-400">
                      {formatCurrency(breakdown.mortgage)}
                    </td>
                  </tr>
                  {breakdown.other_debt !== 0 && (
                    <tr className="border-b">
                      <td className="py-1.5 text-muted-foreground">Other debt</td>
                      <td className="py-1.5 text-right tabular-nums font-medium text-red-600 dark:text-red-400">
                        {formatCurrency(breakdown.other_debt)}
                      </td>
                    </tr>
                  )}
                  <tr className="border-t border-t-foreground/20 font-semibold">
                    <td className="py-2">Net Worth (ex-mortgage)</td>
                    <td className={`py-2 text-right tabular-nums ${breakdown.net_worth_ex_mortgage >= 0 ? "text-green-600 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
                      {formatCurrency(breakdown.net_worth_ex_mortgage)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </CardContent>
          </Card>

          {/* Account-level detail */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Account Detail</CardTitle>
            </CardHeader>
            <CardContent>
              <AccountsTable accounts={breakdown.accounts} />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
