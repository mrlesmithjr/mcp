import { useState } from "react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useChurnAnalysis,
  type Page,
} from "@/lib/api"
import { cn } from "@/lib/utils"

const DAYS_OPTIONS = [30, 60, 90, 180]

function formatCurrency(value: number): string {
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function PeriodSelector({
  options,
  value,
  onChange,
  label,
  suffix,
}: {
  options: number[]
  value: number
  onChange: (v: number) => void
  label: string
  suffix: string
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-muted-foreground">{label}:</span>
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={cn(
            "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
            value === o
              ? "bg-foreground text-background"
              : "border border-input bg-background text-muted-foreground hover:bg-muted",
          )}
        >
          {o}{suffix}
        </button>
      ))}
    </div>
  )
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center h-64 text-muted-foreground">
      Loading {label}...
    </div>
  )
}

function ErrorState({ label }: { label: string }) {
  return (
    <Alert variant="destructive">
      <AlertTitle>Failed to load {label}</AlertTitle>
      <AlertDescription>
        Make sure the API server is running and data is synced (<code>ynab sync</code>).
      </AlertDescription>
    </Alert>
  )
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border bg-muted/20 p-8 text-center">
      <p className="text-sm text-muted-foreground">{message}</p>
      <p className="text-xs text-muted-foreground mt-1">
        Run <code>ynab sync</code> to populate data.
      </p>
    </div>
  )
}

function CalibrateLink({ onNavigate }: { onNavigate: (page: Page) => void }) {
  return (
    <button
      onClick={() => onNavigate("calibration")}
      className="text-xs text-blue-600 hover:underline"
    >
      Calibrate
    </button>
  )
}

function LogTab({
  days,
  onNavigate,
}: {
  days: number
  onNavigate: (page: Page) => void
}) {
  const { data, isLoading, error } = useChurnAnalysis(days)

  if (isLoading) return <LoadingState label="churn data" />
  if (error) return <ErrorState label="churn data" />
  if (!data) return null

  if (data.rows.length === 0) {
    return <EmptyState message="No funding log entries in the selected window." />
  }

  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Category</TableHead>
            <TableHead>Group</TableHead>
            <TableHead className="text-right w-28">Current Target</TableHead>
            <TableHead className="text-right w-20">Moves</TableHead>
            <TableHead className="text-right w-20">Added</TableHead>
            <TableHead className="text-right w-20">Removed</TableHead>
            <TableHead className="text-right w-28">Total Added</TableHead>
            <TableHead className="text-right w-28">Total Removed</TableHead>
            <TableHead className="text-right w-28">Net Change</TableHead>
            <TableHead className="w-32">Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.rows.map((row, i) => (
            <TableRow key={i} className={cn(row.is_churning && "bg-amber-50/50")}>
              <TableCell className="font-medium text-sm">{row.category_name}</TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {row.category_group ?? <span className="italic">uncategorized</span>}
              </TableCell>
              <TableCell className="text-right tabular-nums text-sm">
                {row.current_target != null ? formatCurrency(row.current_target) : <span className="text-muted-foreground">-</span>}
              </TableCell>
              <TableCell className="text-right tabular-nums text-sm">{row.total_moves}</TableCell>
              <TableCell className="text-right tabular-nums text-sm text-green-700">{row.times_added}</TableCell>
              <TableCell className="text-right tabular-nums text-sm text-red-600">{row.times_removed}</TableCell>
              <TableCell className="text-right tabular-nums text-sm">{formatCurrency(row.total_added)}</TableCell>
              <TableCell className="text-right tabular-nums text-sm">{formatCurrency(row.total_removed)}</TableCell>
              <TableCell className={cn(
                "text-right tabular-nums text-sm font-medium",
                row.net_change >= 0 ? "text-green-700" : "text-red-600",
              )}>
                {row.net_change >= 0 ? "+" : ""}{formatCurrency(row.net_change)}
              </TableCell>
              <TableCell>
                {row.is_churning ? (
                  <div className="flex items-center gap-1.5">
                    <Badge variant="outline" className="text-xs bg-amber-50 text-amber-700 border-amber-300">
                      Churn
                    </Badge>
                    <CalibrateLink onNavigate={onNavigate} />
                  </div>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-xs text-muted-foreground text-right">
        {data.rows.length} {data.rows.length === 1 ? "category" : "categories"} &middot; {data.rows.filter((r) => r.is_churning).length} with repeated budget changes
      </p>
    </>
  )
}

export function Churn({ onNavigate }: { onNavigate: (page: Page) => void }) {
  const [days, setDays] = useState(90)

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Funding Log</h2>
        <p className="text-muted-foreground text-sm">
          Categories with repeated funding adjustments: a signal of structural underfunding.
        </p>
      </div>

      <PeriodSelector
        options={DAYS_OPTIONS}
        value={days}
        onChange={setDays}
        label="Lookback"
        suffix="d"
      />

      <LogTab days={days} onNavigate={onNavigate} />
    </div>
  )
}
