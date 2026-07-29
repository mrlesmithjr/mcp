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
import { useAuditLog } from "@/lib/api"
import type { AuditEntry } from "@/lib/api"
import { cn } from "@/lib/utils"

type SourceFilter = "all" | "cli" | "dashboard"

const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "dashboard", label: "Dashboard" },
  { value: "cli", label: "CLI" },
]

const LIMIT_OPTIONS = [50, 100, 250]

function formatTimestamp(iso: string): string {
  return iso.slice(0, 16).replace("T", " ")
}

function ActionBadge({ entry }: { entry: AuditEntry }) {
  const action = entry.action

  if (entry.log_type === "funding") {
    const isIncrease = (entry.delta ?? 0) >= 0
    return (
      <Badge variant="outline" className={cn(
        "text-xs",
        isIncrease
          ? "bg-green-50 text-green-700 border-green-200"
          : "bg-amber-50 text-amber-700 border-amber-200",
      )}>
        {isIncrease ? "funded" : "defunded"}
      </Badge>
    )
  }

  const colorMap: Record<string, string> = {
    "set-goal": "bg-blue-50 text-blue-700 border-blue-200",
    "clear-goal": "bg-slate-50 text-slate-600 border-slate-200",
    "add-planned": "bg-green-50 text-green-700 border-green-200",
    "complete-planned": "bg-green-100 text-green-800 border-green-300",
    "create-transaction": "bg-amber-50 text-amber-700 border-amber-200",
    "update-transaction": "bg-amber-50 text-amber-700 border-amber-200",
    "categorize": "bg-orange-50 text-orange-700 border-orange-200",
    "create-category": "bg-teal-50 text-teal-700 border-teal-200",
    "split-transaction": "bg-amber-50 text-amber-700 border-amber-200",
    "move": "bg-teal-50 text-teal-700 border-teal-200",
  }

  return (
    <Badge variant="outline" className={cn("text-xs", colorMap[action] ?? "bg-slate-50 text-slate-600 border-slate-200")}>
      {action}
    </Badge>
  )
}

function FundingDetails({ details, delta }: { details: string | null; delta?: number | null }) {
  if (!details) return <span className="text-muted-foreground">-</span>

  const parenIdx = details.lastIndexOf("(")
  if (parenIdx === -1 || delta == null) {
    return <span className="text-muted-foreground">{details}</span>
  }

  const before = details.slice(0, parenIdx)
  const paren = details.slice(parenIdx)
  const isPositive = delta >= 0

  return (
    <span>
      <span className="text-muted-foreground">{before}</span>
      <span className={isPositive ? "text-green-600 font-medium" : "text-amber-600 font-medium"}>
        {paren}
      </span>
    </span>
  )
}

function SourceChip({ source }: { source: string }) {
  if (source === "money_movements") {
    return (
      <span className="text-xs px-1.5 py-0.5 rounded bg-teal-50 text-teal-700">
        ynab app
      </span>
    )
  }
  const isDashboard = source === "dashboard" || source.startsWith("dashboard")
  const label = isDashboard ? "dashboard" : "cli"
  return (
    <span className={cn(
      "text-xs px-1.5 py-0.5 rounded",
      isDashboard
        ? "bg-violet-50 text-violet-600"
        : "bg-muted text-muted-foreground",
    )}>
      {label}
    </span>
  )
}

export function Audit() {
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all")
  const [limit, setLimit] = useState(100)

  const { data, isLoading, error } = useAuditLog(limit, sourceFilter)

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">Audit Log</h2>
        <p className="text-muted-foreground text-sm">
          All budget activity: goal changes, funding, planned expenses, and YNAB app moves
        </p>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-4">
        <div className="flex gap-2">
          {SOURCE_OPTIONS.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setSourceFilter(value)}
              className={cn(
                "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                sourceFilter === value
                  ? "bg-foreground text-background"
                  : "border border-input bg-background text-muted-foreground hover:bg-muted",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2 text-sm text-muted-foreground">
          <span>Show</span>
          {LIMIT_OPTIONS.map((n) => (
            <button
              key={n}
              onClick={() => setLimit(n)}
              className={cn(
                "px-2 py-1 rounded text-xs font-medium transition-colors",
                limit === n
                  ? "bg-foreground text-background"
                  : "border border-input bg-background text-muted-foreground hover:bg-muted",
              )}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center h-64 text-muted-foreground">
          Loading audit log...
        </div>
      )}

      {!isLoading && error && (
        <Alert variant="destructive">
          <AlertTitle>Failed to load audit log</AlertTitle>
          <AlertDescription>
            Make sure the API server is running and data is synced (<code>ynab sync</code>).
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !error && data && (
        <>
          {data.entries.length === 0 ? (
            <div className="rounded-lg border bg-muted/20 p-8 text-center">
              <p className="text-sm text-muted-foreground">No audit log entries yet.</p>
              <p className="text-xs text-muted-foreground mt-1">
                Actions taken via the dashboard or CLI will appear here.
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-36">Time</TableHead>
                  <TableHead className="w-44">Action</TableHead>
                  <TableHead>Category / Entity</TableHead>
                  <TableHead>Budgeted Change</TableHead>
                  <TableHead className="w-24">Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.entries.map((entry, i) => (
                  <TableRow key={i}>
                    <TableCell className="tabular-nums text-xs text-muted-foreground whitespace-nowrap">
                      {formatTimestamp(entry.timestamp)}
                    </TableCell>
                    <TableCell>
                      <ActionBadge entry={entry} />
                    </TableCell>
                    <TableCell className="font-medium text-sm">
                      {entry.name ?? <span className="text-muted-foreground">-</span>}
                    </TableCell>
                    <TableCell className="text-sm max-w-xs truncate" title={entry.details ?? ""}>
                      {entry.log_type === "funding"
                        ? <FundingDetails details={entry.details} delta={entry.delta} />
                        : <span className="text-muted-foreground">{entry.details ?? "-"}</span>
                      }
                    </TableCell>
                    <TableCell>
                      <SourceChip source={entry.source} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <p className="text-xs text-muted-foreground text-right">
            {data.count} {data.count === 1 ? "entry" : "entries"}
          </p>
        </>
      )}
    </div>
  )
}
