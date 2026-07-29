import { useState } from "react"
import { AlertTriangle, ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react"
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useReport, formatCurrency } from "@/lib/api"
import type { ReportRow } from "@/lib/api"

type SortKey = "label" | "total" | "monthly_avg"
type SortDir = "asc" | "desc"

interface SortableHeadProps {
  label: string
  col: SortKey
  sortKey: SortKey
  sortDir: SortDir
  onSort: (col: SortKey) => void
  align?: "left" | "right"
}

function SortableHead({ label, col, sortKey, sortDir, onSort, align = "left" }: SortableHeadProps) {
  const active = sortKey === col
  const Icon = active ? (sortDir === "asc" ? ChevronUp : ChevronDown) : ChevronsUpDown
  return (
    <TableHead
      className={`cursor-pointer select-none ${align === "right" ? "text-right pr-4" : "pl-4"}`}
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        <Icon className={`h-3 w-3 ${active ? "opacity-100" : "opacity-30"}`} />
      </span>
    </TableHead>
  )
}

function compactCurrency(v: number): string {
  if (v >= 1000) return `$${(v / 1000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function groupByLabel(groupBy: string): string {
  if (groupBy === "category_group") return "Category Group"
  if (groupBy === "category") return "Category"
  return "Payee"
}

export function CustomReport() {
  const [groupBy, setGroupBy] = useState("category_group")
  const [metric, setMetric] = useState("spending")
  const [months, setMonths] = useState(12)
  const [sortKey, setSortKey] = useState<SortKey>("total")
  const [sortDir, setSortDir] = useState<SortDir>("desc")

  const { data, isLoading, error } = useReport(groupBy, metric, months)

  function handleSort(col: SortKey) {
    if (sortKey === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(col)
      setSortDir("desc")
    }
  }

  function handleGroupByChange(value: string) {
    setGroupBy(value)
    if (value === "payee" && metric === "budgeted") setMetric("spending")
  }

  const chartRows: ReportRow[] = data?.rows.slice(0, 15) ?? []

  const tableRows: ReportRow[] = data
    ? [...data.rows].sort((a, b) => {
        const mul = sortDir === "asc" ? 1 : -1
        if (sortKey === "label") return mul * a.label.localeCompare(b.label)
        return mul * (a[sortKey] - b[sortKey])
      })
    : []

  const metricLabel = metric === "spending" ? "Spending" : "Budgeted"

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Custom Report</h2>
        <p className="text-muted-foreground text-sm">Ad-hoc analysis across any dimension</p>
      </div>

      <div className="flex flex-wrap gap-4 items-center">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Group by</span>
          <select
            value={groupBy}
            onChange={(e) => handleGroupByChange(e.target.value)}
            className="border rounded px-2 py-1 text-sm bg-background"
          >
            <option value="category_group">Category Group</option>
            <option value="category">Category</option>
            <option value="payee">Payee</option>
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Metric</span>
          <select
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
            className="border rounded px-2 py-1 text-sm bg-background"
            disabled={groupBy === "payee"}
          >
            <option value="spending">Spending</option>
            <option value="budgeted">Budgeted</option>
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Range</span>
          <select
            value={months}
            onChange={(e) => setMonths(Number(e.target.value))}
            className="border rounded px-2 py-1 text-sm bg-background"
          >
            <option value={3}>Last 3 months</option>
            <option value={6}>Last 6 months</option>
            <option value={12}>Last 12 months</option>
            <option value={24}>Last 24 months</option>
          </select>
        </label>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center h-64 text-muted-foreground">
          Loading report...
        </div>
      )}

      {!isLoading && error && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Failed to load report</AlertTitle>
          <AlertDescription>
            Make sure the API server is running and data is synced (<code>ynab sync</code>).
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !error && data && data.rows.length === 0 && (
        <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
          No data for this combination. Try a different range or metric.
        </div>
      )}

      {!isLoading && !error && data && data.rows.length > 0 && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                {metricLabel} by {groupByLabel(groupBy)}
                <span className="font-normal text-muted-foreground ml-2 text-sm">
                  last {months} months
                  {chartRows.length < data.rows.length ? `, top ${chartRows.length} shown` : ""}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={Math.max(200, chartRows.length * 34)}>
                <BarChart
                  layout="vertical"
                  data={chartRows}
                  margin={{ top: 4, right: 24, bottom: 4, left: 0 }}
                >
                  <CartesianGrid horizontal={false} strokeDasharray="3 3" />
                  <XAxis
                    type="number"
                    tickLine={false}
                    axisLine={false}
                    tick={{ fontSize: 11 }}
                    tickFormatter={compactCurrency}
                  />
                  <YAxis
                    type="category"
                    dataKey="label"
                    width={160}
                    tickLine={false}
                    axisLine={false}
                    tick={{ fontSize: 12 }}
                  />
                  <Tooltip
                    formatter={(value) => [formatCurrency(Number(value ?? 0)), metricLabel]}
                    cursor={{ fill: "hsl(var(--muted))" }}
                  />
                  <Bar dataKey="total" fill="oklch(0.6 0.18 220)" radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">All Results</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHead label="Name" col="label" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                    <SortableHead label="Total" col="total" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} align="right" />
                    <SortableHead label="Monthly Avg" col="monthly_avg" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} align="right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tableRows.map((row) => (
                    <TableRow key={row.label}>
                      <TableCell className="pl-4">{row.label}</TableCell>
                      <TableCell className="text-right pr-2 tabular-nums">{formatCurrency(row.total)}</TableCell>
                      <TableCell className="text-right pr-4 tabular-nums text-muted-foreground">{formatCurrency(row.monthly_avg)}</TableCell>
                    </TableRow>
                  ))}
                  <TableRow className="border-t-2 font-medium bg-muted/30">
                    <TableCell className="pl-4 text-sm">Total ({data.rows.length} {groupByLabel(groupBy).toLowerCase()}s)</TableCell>
                    <TableCell className="text-right pr-2 tabular-nums text-sm">
                      {formatCurrency(data.rows.reduce((sum, r) => sum + r.total, 0))}
                    </TableCell>
                    <TableCell className="text-right pr-4 tabular-nums text-sm text-muted-foreground">
                      {formatCurrency(data.rows.reduce((sum, r) => sum + r.monthly_avg, 0))}/mo
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
