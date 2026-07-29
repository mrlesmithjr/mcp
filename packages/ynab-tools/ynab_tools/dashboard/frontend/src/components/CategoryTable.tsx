import * as React from "react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { formatCurrency } from "@/lib/api"
import type { CategoryData } from "@/lib/api"

const STATUS_CONFIG = {
  ON_TRACK: { label: "On Track", className: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 border-0" },
  RUNNING_HOT: { label: "Running Hot", className: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400 border-0" },
  OVERSPENT: { label: "Overspent", className: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400 border-0" },
  UNDERSPENT: { label: "Underspent", className: "bg-muted text-muted-foreground border-0" },
}

interface CategoryTableProps {
  categories: CategoryData[]
}

export function CategoryTable({ categories }: CategoryTableProps) {
  // Group categories, skip groups with no budgeted or spent activity
  const grouped: Record<string, CategoryData[]> = {}
  for (const cat of categories) {
    if (cat.budgeted === 0 && cat.spent === 0) continue
    if (!grouped[cat.group]) grouped[cat.group] = []
    grouped[cat.group].push(cat)
  }

  const groups = Object.entries(grouped)
  if (groups.length === 0) return <p className="text-muted-foreground text-sm">No category data.</p>

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Category</TableHead>
          <TableHead className="text-right">Budgeted</TableHead>
          <TableHead className="text-right">Spent</TableHead>
          <TableHead className="text-right">Remaining</TableHead>
          <TableHead className="w-40">Progress</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.map(([group, cats]) => (
          <React.Fragment key={group}>
            <TableRow className="bg-muted/40 hover:bg-muted/40">
              <TableCell colSpan={6} className="py-1.5 font-semibold text-xs text-muted-foreground uppercase tracking-wide">
                {group}
              </TableCell>
            </TableRow>
            {cats.map((cat) => {
              const status = STATUS_CONFIG[cat.status]
              const pctCapped = Math.min(cat.pct_used, 100)
              const barColor =
                cat.status === "OVERSPENT" ? "bg-red-500" :
                cat.status === "RUNNING_HOT" ? "bg-amber-500" :
                "bg-primary"

              return (
                <TableRow
                  key={`${group}-${cat.name}`}
                  className={cn(
                    cat.status === "OVERSPENT" && "bg-red-50/50 dark:bg-red-950/20",
                    cat.status === "RUNNING_HOT" && "bg-amber-50/50 dark:bg-amber-950/20",
                  )}
                >
                  <TableCell className="font-medium">{cat.name}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatCurrency(cat.budgeted)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatCurrency(cat.spent)}</TableCell>
                  <TableCell className={cn(
                    "text-right tabular-nums",
                    cat.balance < 0 ? "text-red-600 dark:text-red-400" : "text-muted-foreground"
                  )}>
                    {formatCurrency(cat.balance)}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-muted rounded-full h-1.5">
                        <div
                          className={cn("h-1.5 rounded-full transition-all", barColor)}
                          style={{ width: `${pctCapped}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted-foreground w-10 text-right tabular-nums">
                        {cat.pct_used.toFixed(0)}%
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge className={status.className}>{status.label}</Badge>
                  </TableCell>
                </TableRow>
              )
            })}
          </React.Fragment>
        ))}
      </TableBody>
    </Table>
  )
}
