import type { ReactNode } from "react"
import { Info } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface KPICardProps {
  label: string
  value: string
  subtitle?: string
  tooltip?: string
  variant?: "default" | "positive" | "negative" | "neutral"
  badge?: ReactNode
}

export function KPICard({ label, value, subtitle, tooltip, variant = "default", badge }: KPICardProps) {
  const valueColor = {
    default: "text-foreground",
    positive: "text-green-600 dark:text-green-400",
    negative: "text-red-600 dark:text-red-400",
    neutral: "text-muted-foreground",
  }[variant]

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-1">
          {label}
          {tooltip && (
            <span title={tooltip} className="cursor-help inline-flex items-center">
              <Info className="h-3 w-3 text-muted-foreground/50 hover:text-muted-foreground transition-colors" />
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className={cn("text-2xl font-bold tabular-nums", valueColor)}>{value}</div>
        {badge}
        {subtitle && <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>}
      </CardContent>
    </Card>
  )
}
