/**
 * PrimaryActionBanner - synthesized next-action recommendation for Overview.
 *
 * Fetches /api/overview/primary-action and renders a full-width banner.
 * Color coding:
 *   - all_good           → subtle green strip
 *   - fund_rta / fund_goals → amber alert
 *   - unapproved / structural_overspend → red alert
 *
 * refs #187
 */
import { AlertTriangle, CheckCircle2, Info } from "lucide-react"
import { usePrimaryAction } from "@/lib/api"
import type { Page } from "@/lib/api"

interface PrimaryActionBannerProps {
  onNavigate: (page: Page) => void
}

function priorityToPage(actionPath: string | null): Page | null {
  if (!actionPath) return null
  const map: Record<string, Page> = {
    "/unapproved": "unapproved",
    "/paycheck-funding": "paycheck-funding",
    "/sinking-funds": "sinking-funds",
    "/overspend-plan": "overspend-plan",
  }
  return map[actionPath] ?? null
}

export function PrimaryActionBanner({ onNavigate }: PrimaryActionBannerProps) {
  const { data, isLoading, error } = usePrimaryAction()

  // Loading skeleton
  if (isLoading) {
    return (
      <div className="h-10 rounded-lg bg-muted/50 animate-pulse" />
    )
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-muted px-4 py-2.5 text-xs text-muted-foreground">
        Could not load action recommendation.
      </div>
    )
  }

  if (!data) return null

  const targetPage = priorityToPage(data.action_path)

  if (data.priority === "all_good") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20 px-4 py-2.5">
        <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600 dark:text-green-400" />
        <span className="text-sm text-green-800 dark:text-green-300">{data.message}</span>
      </div>
    )
  }

  const isRed = data.priority === "unapproved" || data.priority === "structural_overspend"
  const isAmber = data.priority === "fund_rta" || data.priority === "fund_goals"

  const containerClass = isRed
    ? "border-rose-200 bg-rose-50 dark:border-rose-800 dark:bg-rose-950/20"
    : isAmber
    ? "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20"
    : "border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/20"

  const iconClass = isRed
    ? "text-rose-600 dark:text-rose-400"
    : isAmber
    ? "text-amber-600 dark:text-amber-400"
    : "text-blue-600 dark:text-blue-400"

  const textClass = isRed
    ? "text-rose-800 dark:text-rose-300"
    : isAmber
    ? "text-amber-800 dark:text-amber-300"
    : "text-blue-800 dark:text-blue-300"

  const secondaryClass = isRed
    ? "text-rose-600 dark:text-rose-400"
    : isAmber
    ? "text-amber-600 dark:text-amber-400"
    : "text-blue-600 dark:text-blue-400"

  const linkClass = isRed
    ? "text-rose-700 dark:text-rose-300 font-semibold hover:underline underline-offset-2 shrink-0"
    : isAmber
    ? "text-amber-700 dark:text-amber-300 font-semibold hover:underline underline-offset-2 shrink-0"
    : "text-blue-700 dark:text-blue-300 font-semibold hover:underline underline-offset-2 shrink-0"

  const Icon = isRed || isAmber ? AlertTriangle : Info

  return (
    <div className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${containerClass}`}>
      <Icon className={`h-4 w-4 shrink-0 mt-0.5 ${iconClass}`} />
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-medium ${textClass}`}>{data.message}</p>
        {data.detail && (
          <p className={`text-xs mt-0.5 ${secondaryClass}`}>{data.detail}</p>
        )}
      </div>
      {targetPage && (
        <button
          onClick={() => onNavigate(targetPage)}
          className={`text-xs ${linkClass}`}
        >
          Take action
        </button>
      )}
    </div>
  )
}
