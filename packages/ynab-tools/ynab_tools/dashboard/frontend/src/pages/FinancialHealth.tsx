import { useState } from "react"
import { cn } from "@/lib/utils"
import { Trends } from "@/pages/Trends"
import { BudgetFit } from "@/pages/BudgetFit"
import type { FilterOption } from "@/lib/api"

type Tab = "trends" | "budget-fit"

function TabButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
        active
          ? "bg-foreground text-background"
          : "border border-input bg-background text-muted-foreground hover:bg-muted",
      )}
    >
      {label}
    </button>
  )
}

export function FinancialHealth({
  onNavigateToCalibration,
}: {
  onNavigateToCalibration: (filter: FilterOption) => void
}) {
  const [activeTab, setActiveTab] = useState<Tab>("trends")

  return (
    <div className="space-y-6">
      {/* Tab selector; page heading comes from the active tab's content */}
      <div className="flex items-center gap-2">
        <TabButton label="Trends" active={activeTab === "trends"} onClick={() => setActiveTab("trends")} />
        <TabButton label="Budget Fit" active={activeTab === "budget-fit"} onClick={() => setActiveTab("budget-fit")} />
      </div>

      {activeTab === "trends" && <Trends />}
      {activeTab === "budget-fit" && (
        <BudgetFit onNavigateToCalibration={onNavigateToCalibration} />
      )}
    </div>
  )
}
