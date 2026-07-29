import { useState } from "react"
import { ChevronDown, ChevronRight, CheckCircle2, Info } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { KPICard } from "@/components/KPICard"
import { usePaycheckFunding, useApplyPaycheckFunding, formatCurrency } from "@/lib/api"
import type { PaycheckFundingTier } from "@/lib/api"
import { cn } from "@/lib/utils"
import { toast } from "sonner"

const TIER_COLORS: Record<number, string> = {
  1: "text-red-700 dark:text-red-400",
  2: "text-amber-700 dark:text-amber-400",
  3: "text-blue-700 dark:text-blue-400",
  4: "text-purple-700 dark:text-purple-400",
  5: "text-slate-600 dark:text-slate-400",
}

function formatReason(reason: string, tier: number): string {
  if (!reason) return ""
  if (tier === 1) return reason.replace(/^overspent/, "Overdrawn by")
  if (tier === 4) {
    const m = reason.match(/^~\$([0-9.]+)\/day x (\d+)d \(bal \$([0-9.-]+)\)/)
    if (m) return `Bridge: $${m[1]}/day for ${m[2]} days (balance: $${m[3]})`
  }
  return reason
}

function InfoPanel() {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-md border border-border bg-muted/30 text-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 w-full px-4 py-2 text-left text-muted-foreground hover:text-foreground transition-colors"
      >
        <Info className="h-4 w-4 shrink-0" />
        <span>How does this work?</span>
        {open ? <ChevronDown className="h-3 w-3 ml-auto" /> : <ChevronRight className="h-3 w-3 ml-auto" />}
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-3 text-xs text-muted-foreground">
          <p>
            Categories are grouped into five priority tiers. Fund higher tiers first; lower tiers are only touched when
            higher ones are fully covered. Clicking <strong>Fund through Tier N</strong> applies your RTA to{" "}
            <em>all</em> tiers 1 through N at once.
          </p>
          <div className="space-y-1.5">
            <div>
              <span className="font-semibold text-red-700 dark:text-red-400">Tier 1: Cover Overspent</span>{" "}
              Categories already in the red. Fix these first or YNAB tracking breaks.
            </div>
            <div>
              <span className="font-semibold text-amber-700 dark:text-amber-400">Tier 2: Due Soon</span>{" "}
              Planned expenses due within 14 days. Deadlines are imminent.
            </div>
            <div>
              <span className="font-semibold text-blue-700 dark:text-blue-400">Tier 3: Monthly Bills</span>{" "}
              Fixed recurring bills (loans, insurance, subscriptions). These autopay on a schedule and cannot wait.
            </div>
            <div>
              <span className="font-semibold text-purple-700 dark:text-purple-400">Tier 4: Bridge Daily Spending</span>{" "}
              Groceries, gas, dining. Keep enough balance to last until your next paycheck.
            </div>
            <div>
              <span className="font-semibold text-slate-600 dark:text-slate-400">Tier 5: Remaining Goals</span>{" "}
              Everything else underfunded. Fund if surplus allows.
            </div>
          </div>
          <p>
            <strong>Tiers 1-3 are protected:</strong> non-negotiable obligations that must be funded before anything
            discretionary. Tiers 4-5 are fine to fund partially if RTA runs short.
          </p>
          <p>
            <strong>Why are savings and retirement missing?</strong> Those categories are funded from a separate bonus
            paycheck (the two-pot rule). Only regular-paycheck categories appear here.
          </p>
        </div>
      )}
    </div>
  )
}

function TierCard({
  tier,
  description,
  remainingRta,
  funded,
  onFund,
  isPending,
}: {
  tier: PaycheckFundingTier
  description: string
  remainingRta: number
  funded: boolean
  onFund: (throughTier: number) => void
  isPending: boolean
}) {
  const [open, setOpen] = useState(tier.tier <= 2)
  const canFund = tier.total > 0 && remainingRta > 0 && !funded
  const colorClass = TIER_COLORS[tier.tier] ?? "text-foreground"

  const rtaNote =
    !funded && tier.total > 0
      ? remainingRta <= 0
        ? { text: "Ready to Assign exhausted. Fund with next paycheck.", cls: "text-red-600 dark:text-red-400" }
        : remainingRta < tier.total
          ? {
              text: `Partially fundable: ${formatCurrency(remainingRta)} of ${formatCurrency(tier.total)} available`,
              cls: "text-amber-600 dark:text-amber-400",
            }
          : null
      : null

  return (
    <Card className={cn(funded && "opacity-60")}>
      <CardHeader className="pb-2 cursor-pointer" onClick={() => setOpen((o) => !o)}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            {open ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
            )}
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <CardTitle className={cn("text-sm font-semibold", colorClass)}>
                  Tier {tier.tier}: {tier.label}
                </CardTitle>
                {funded && <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />}
              </div>
              {description && (
                <p className="text-xs text-muted-foreground font-normal leading-snug mt-0.5">{description}</p>
              )}
              {rtaNote && <p className={cn("text-xs font-normal mt-0.5", rtaNote.cls)}>{rtaNote.text}</p>}
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0 ml-4">
            <span className="text-sm font-medium">{formatCurrency(tier.total)}</span>
            {canFund && (
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onFund(tier.tier)
                }}
                disabled={isPending}
                className={cn(
                  "px-3 py-1 text-xs font-medium rounded-md border transition-colors",
                  isPending
                    ? "opacity-60 cursor-not-allowed border-input bg-muted text-muted-foreground"
                    : "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
                )}
              >
                {isPending ? "Funding..." : `Fund through Tier ${tier.tier}`}
              </button>
            )}
          </div>
        </div>
      </CardHeader>
      {open && (
        <CardContent>
          {tier.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing to fund in this tier.</p>
          ) : (
            <div className="space-y-0">
              {tier.items.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between py-2 border-b last:border-b-0 text-sm"
                >
                  <div className="min-w-0">
                    <span className="font-medium truncate block">{item.name}</span>
                    {item.group && <span className="text-xs text-muted-foreground">{item.group}</span>}
                  </div>
                  <div className="text-right ml-4 shrink-0">
                    <div className="font-medium">{formatCurrency(item.amount_needed)}</div>
                    {item.reason && (
                      <div className="text-xs text-muted-foreground">{formatReason(item.reason, tier.tier)}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

export function PaycheckFunding() {
  const { data, isLoading, error } = usePaycheckFunding()
  const { mutate: applyFunding, isPending } = useApplyPaycheckFunding()
  const [fundedThrough, setFundedThrough] = useState<number | null>(null)

  if (isLoading) return <div className="text-sm text-muted-foreground">Loading...</div>
  if (error || !data) return <div className="text-sm text-destructive">Failed to load paycheck funding data.</div>

  function handleFund(throughTier: number) {
    applyFunding(
      { through_tier: throughTier },
      {
        onSuccess: (result) => {
          setFundedThrough(throughTier)
          if (result.skipped.length > 0) {
            const names = result.skipped.map((s) => s.name).join(", ")
            toast.success(
              `Funded ${result.funded.length} categories (${formatCurrency(result.total_funded)} applied). Skipped: ${names}`,
            )
          } else {
            toast.success(`Funded ${result.funded.length} categories (${formatCurrency(result.total_funded)} applied)`)
          }
        },
        onError: (err) => toast.error(`Funding failed: ${err.message}`),
      },
    )
  }

  let remainingRta = data.rta
  const tierRemainingMap: Record<number, number> = {}
  for (const tier of data.tiers) {
    tierRemainingMap[tier.tier] = remainingRta
    remainingRta -= tier.total
  }

  const surplus = data.rta - data.total_needed
  const isProtectedCovered = data.is_protected_covered ?? data.rta >= data.protected_total
  const tierDescriptions = data.tier_descriptions ?? {}

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">Paycheck Funding</h2>
        <p className="text-sm text-muted-foreground">
          Priority-ordered funding plan for {data.month}.
          {data.next_income && (
            <span>
              {" "}
              Next paycheck: {formatCurrency(data.next_income.amount)} on{" "}
              {new Date(data.next_income.date + "T00:00:00").toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
              })}
              {data.next_income.is_bonus && " (includes bonus)"}
            </span>
          )}
        </p>
      </div>

      <InfoPanel />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <KPICard
          label="Ready to Assign"
          value={formatCurrency(data.rta)}
          variant={data.rta < 0 ? "negative" : "default"}
        />
        <KPICard
          label="Months Covered"
          value={
            data.protected_total > 0
              ? `${(data.rta / data.protected_total).toFixed(1)} mo`
              : "-"
          }
          subtitle="RTA ÷ T1-T3 obligations"
          variant={
            data.protected_total <= 0
              ? "default"
              : data.rta / data.protected_total >= 1
                ? "default"
                : "negative"
          }
          tooltip="How many months of Tier 1-3 fixed obligations the current Ready to Assign balance would cover. Less than 1.0 means RTA cannot fully cover protected obligations."
        />
        <KPICard label="Total Needed" value={formatCurrency(data.total_needed)} variant="default" />
        <KPICard
          label={surplus >= 0 ? "Surplus" : "Gap"}
          value={formatCurrency(Math.abs(surplus))}
          variant={surplus < 0 ? "neutral" : "default"}
        />
      </div>

      {isProtectedCovered ? (
        <div className="rounded-md border border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 px-4 py-3 text-sm text-emerald-800 dark:text-emerald-300">
          Your RTA covers all fixed obligations (Tiers 1-3, {formatCurrency(data.protected_total)}). Fund through Tier
          3 to lock in your bills, then decide how much discretionary spending (Tiers 4-5) to add.
        </div>
      ) : (
        <div className="rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
          Your RTA ({formatCurrency(data.rta)}) falls short of fixed obligations (
          {formatCurrency(data.protected_total)}). Fund what you can: Tier 1 first, then Tier 2, then Tier 3.
        </div>
      )}

      {fundedThrough !== null && (
        <div className="rounded-md bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 px-4 py-3 text-sm text-emerald-800 dark:text-emerald-300">
          Funded through Tier {fundedThrough}. Balances updated.
        </div>
      )}

      <div className="space-y-3">
        {data.tiers.map((tier) => (
          <TierCard
            key={tier.tier}
            tier={tier}
            description={tierDescriptions[tier.tier] ?? ""}
            remainingRta={tierRemainingMap[tier.tier] ?? 0}
            funded={fundedThrough !== null && tier.tier <= fundedThrough}
            onFund={handleFund}
            isPending={isPending}
          />
        ))}
      </div>
    </div>
  )
}
