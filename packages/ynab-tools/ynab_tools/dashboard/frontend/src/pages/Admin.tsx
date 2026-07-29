import { useState, useEffect, useMemo, Fragment } from "react"
import { AlertTriangle, ChevronDown, Eye, EyeOff, Info, XCircle } from "lucide-react"
import { toast } from "sonner"
import { Alert } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog"
import { useAdminConfig, useAdminValidation, useUpdateConfig, useFetchBudgets, useDeriveIncome } from "@/lib/api"
import type { AdminValidationData, DeriveIncomeResult, ValidationRecommendations } from "@/lib/api"
import { ValidatedTagInput } from "@/components/admin/ValidatedTagInput"
import { MilestoneEditor } from "@/components/admin/MilestoneEditor"
import { FidelityAccountEditor } from "@/components/admin/FidelityAccountEditor"
import { PairEditor } from "@/components/admin/PairEditor"

// ---- Format validators -------------------------------------------------------

function isUUID(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)
}
function isValidYear(s: string): boolean {
  const y = parseInt(s, 10)
  return /^\d{4}$/.test(s) && y >= 1920 && y <= 2010
}
function isNumeric(s: string): boolean {
  return s === "" || /^\d+(\.\d+)?$/.test(s.trim())
}
function matchesSuggestion(val: string, suggestions: string[]): boolean {
  const v = val.trim().toLowerCase()
  return suggestions.some((s) => s.toLowerCase() === v)
}
function fragmentMatchesSuggestion(val: string, suggestions: string[]): boolean {
  const v = val.trim().toLowerCase()
  return v.length > 0 && suggestions.some((s) => s.toLowerCase().includes(v))
}
function isTagKnown(tag: string, suggestions: string[], matchType: "exact" | "fragment" = "exact"): boolean {
  if (suggestions.length === 0) return true
  return matchType === "fragment" ? fragmentMatchesSuggestion(tag, suggestions) : matchesSuggestion(tag, suggestions)
}

// ---- ValidatedTextInput -----------------------------------------------------

function ValidatedTextInput({
  value,
  onChange,
  suggestions,
  fieldId,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  suggestions: string[]
  fieldId: string
  placeholder?: string
}) {
  const isUnrecognized =
    suggestions.length > 0 &&
    value.trim() !== "" &&
    !matchesSuggestion(value, suggestions)

  return (
    <div className="space-y-1">
      <input
        list={`${fieldId}-list`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
      />
      <datalist id={`${fieldId}-list`}>
        {suggestions.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
      {isUnrecognized && (
        <p className="text-xs text-amber-700 dark:text-amber-400 flex items-center gap-1">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          Account not found in YNAB data
        </p>
      )}
    </div>
  )
}

// ---- BudgetPicker -----------------------------------------------------------

function BudgetPicker({
  token,
  onSelect,
}: {
  token: string
  onSelect: (id: string, name: string) => void
}) {
  const { mutate: fetchBudgets, isPending, data, error, reset } = useFetchBudgets()
  const [open, setOpen] = useState(false)

  function handleClick() {
    reset()
    setOpen(false)
    fetchBudgets(
      { access_token: token || undefined },
      {
        onSuccess: (d) => {
          if (d.budgets.length === 1) {
            onSelect(d.budgets[0].id, d.budgets[0].name)
          } else {
            setOpen(true)
          }
        },
      }
    )
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={handleClick}
        disabled={!token.trim() || isPending}
        className="text-xs text-primary hover:underline underline-offset-2 disabled:opacity-40 disabled:no-underline disabled:cursor-not-allowed"
      >
        {isPending ? "Fetching..." : "Select from YNAB"}
      </button>
      {error && (
        <p className="text-xs text-destructive mt-0.5">{error.message}</p>
      )}
      {open && data && data.budgets.length > 1 && (
        <div className="absolute z-50 mt-1 rounded-md border border-input bg-background shadow-md w-72">
          {data.budgets.map((b) => (
            <button
              key={b.id}
              type="button"
              onClick={() => { onSelect(b.id, b.name); setOpen(false) }}
              className="w-full text-left px-3 py-2 text-sm hover:bg-muted"
            >
              <span className="font-medium">{b.name}</span>
              <span className="block text-xs text-muted-foreground font-mono">{b.id}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---- PasswordInput ----------------------------------------------------------

function PasswordInput({
  value,
  onChange,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  const [show, setShow] = useState(false)
  return (
    <div className="relative">
      <input
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-input bg-background px-3 py-2 pr-9 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-ring"
      />
      <button
        type="button"
        onClick={() => setShow(!show)}
        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
      >
        {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  )
}

// ---- FormField --------------------------------------------------------------

function FormField({
  label,
  help,
  error,
  required,
  children,
}: {
  label: string
  help?: string
  error?: string | null
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium leading-none flex items-center gap-1.5">
        {label}
        {required && <span className="text-xs font-normal text-destructive">Required</span>}
      </label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
      {!error && help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  )
}

// ---- Field definitions ------------------------------------------------------

function normalizeNumericStr(raw: unknown): string {
  if (raw == null) return ""
  const n = Number(raw)
  if (!isNaN(n) && Number.isFinite(n)) return String(Math.round(n))
  return String(raw)
}

type FieldType = "text" | "password" | "number" | "tags" | "textarea" | "validated-tags" | "validated-text" | "milestone-list" | "fidelity-accounts" | "pair-editor"
type SuggestionPool = "groups" | "categories" | "accounts" | "payees"

const POOL_LABELS: Record<SuggestionPool, string> = {
  groups: "category groups",
  categories: "categories",
  accounts: "accounts",
  payees: "payees",
}

interface FieldDef {
  key: string
  label: string
  type: FieldType
  suggestions?: SuggestionPool | SuggestionPool[]
  matchType?: "exact" | "fragment"
  recommendationKey?: keyof ValidationRecommendations
  groupCoveredBy?: string
  readonly?: boolean
  required?: boolean
  dividerBefore?: string
  help?: string
  placeholder?: string
  prefix?: string
  suffix?: string
  col1Label?: string
  col2Label?: string
}

function resolvePool(suggestions: SuggestionPool | SuggestionPool[] | undefined, data: { groups: string[]; categories: string[]; accounts: string[]; payees: string[] } | undefined): string[] {
  if (!suggestions) return []
  if (Array.isArray(suggestions)) return [...new Set(suggestions.flatMap((s) => data?.[s] ?? []))]
  return data?.[suggestions] ?? []
}
function resolvePoolLabel(suggestions: SuggestionPool | SuggestionPool[] | undefined): string {
  if (!suggestions) return ""
  if (Array.isArray(suggestions)) return suggestions.map((s) => POOL_LABELS[s]).join(" or ")
  return POOL_LABELS[suggestions]
}

interface GroupDef {
  title: string
  description?: string
  fields: FieldDef[]
}

const FIELD_GROUPS: GroupDef[] = [
  {
    title: "YNAB Connection",
    fields: [
      {
        key: "access_token",
        label: "Access Token",
        type: "password",
        placeholder: "ynab_...",
        required: true,
        help: "Personal access token from app.ynab.com: Account Settings > Developer Settings",
      },
      {
        key: "plan_id",
        label: "Budget ID",
        type: "text",
        readonly: true,
        required: true,
        help: "Use 'Select from YNAB' to populate.",
      },
      {
        key: "plan_name",
        label: "Budget Name",
        type: "text",
        readonly: true,
        help: "Auto-populated by ynab sync. Not editable.",
      },
    ],
  },
  {
    title: "Income",
    description: "Paycheck detection and salary data for income reports, paycheck forecasting, and bonus identification.",
    fields: [
      {
        key: "paycheck_payees",
        label: "Paycheck Payees",
        type: "validated-tags",
        suggestions: "payees",
        help: "Payee name(s) used for direct deposit. Required for 'Calculate from transactions' and paycheck reports.",
      },
      {
        key: "regular_pay",
        label: "Regular Paycheck (net)",
        type: "number",
        placeholder: "0",
        prefix: "$",
        help: "Net take-home amount per regular paycheck",
      },
      {
        key: "bonus_threshold",
        label: "Bonus Threshold",
        type: "number",
        placeholder: "0",
        prefix: "$",
        help: "Paycheck income above this amount is treated as a bonus",
      },
      {
        key: "gross_salary",
        label: "Gross Base Salary",
        type: "number",
        placeholder: "0",
        prefix: "$",
        help: "Annual base salary. Used for budget health ratio calculations.",
      },
      {
        key: "gross_ote",
        label: "Gross OTE (On-Target Earnings)",
        type: "number",
        placeholder: "0",
        prefix: "$",
        help: "On-target earnings including expected bonus",
      },
    ],
  },
  {
    title: "Health Ratios",
    description: "Category groups and names used to calculate housing, auto, and debt ratios in the budget health report.",
    fields: [
      {
        key: "ratio_housing",
        label: "Housing",
        type: "validated-tags",
        suggestions: "categories",
        recommendationKey: "ratio_housing",
        placeholder: "Mortgage & Rent",
        help: "Category name(s) counted as housing expense",
      },
      {
        key: "ratio_auto",
        label: "Auto",
        type: "validated-tags",
        suggestions: "categories",
        recommendationKey: "ratio_auto",
        placeholder: "Explorer, Jeep",
        help: "Category names counted as auto expense",
      },
      {
        key: "ratio_debt",
        label: "Debt Service",
        type: "validated-tags",
        suggestions: "categories",
        recommendationKey: "ratio_debt",
        placeholder: "Mortgage & Rent, Student Loan",
        help: "Category names counted as debt payments",
      },
    ],
  },
  {
    title: "Retirement",
    description: "Account tracking and contribution data for retirement balance summaries and growth projections.",
    fields: [
      {
        key: "birth_year",
        label: "Birth Year",
        type: "number",
        placeholder: "1972",
        help: "Used for retirement timeline projections and milestone age filtering",
      },
      {
        key: "retirement_401k",
        label: "401k Account",
        type: "validated-text",
        suggestions: "accounts",
        recommendationKey: "retirement_401k",
        help: "YNAB account name tracking your 401k balance",
      },
      {
        key: "employer_match_pct",
        label: "Employer Match %",
        type: "number",
        placeholder: "0",
        suffix: "%",
        help: "Employer 401k match rate (e.g. 6 for 100% match up to 6% of compensation). Applied to gross OTE for projection.",
      },
      {
        key: "retirement_roth_ira",
        label: "Roth IRA Account",
        type: "validated-text",
        suggestions: "accounts",
        recommendationKey: "retirement_roth_ira",
        help: "YNAB account name tracking your Roth IRA",
      },
      {
        key: "retirement_trad_ira",
        label: "Traditional IRA Account",
        type: "validated-text",
        suggestions: "accounts",
        recommendationKey: "retirement_trad_ira",
        help: "YNAB account name tracking your Traditional IRA",
      },
      {
        key: "retirement_taxable",
        label: "Taxable Brokerage",
        type: "validated-text",
        suggestions: "accounts",
        recommendationKey: "retirement_taxable",
        help: "YNAB account name tracking your taxable brokerage",
      },
      {
        key: "retirement_annual",
        label: "Annual Contribution",
        type: "number",
        placeholder: "0",
        prefix: "$",
        help: "Total annual retirement contributions across all accounts",
      },
      {
        key: "retirement_rate",
        label: "Projected Return Rate",
        type: "number",
        placeholder: "7",
        suffix: "%",
        help: "Nominal annual return rate used for portfolio projection. Default is 7%.",
      },
      {
        key: "retirement_milestones",
        label: "Milestones",
        type: "milestone-list",
        help: "Age and label for each retirement milestone. Suggestions filter to future ages based on Birth Year.",
      },
    ],
  },
  {
    title: "Budget Structure",
    description: "Controls which categories are included or excluded in auto-funding and budget analysis.",
    fields: [
      {
        key: "protected_groups",
        label: "Protected Groups",
        type: "validated-tags",
        suggestions: ["groups", "categories"] as SuggestionPool[],
        matchType: "fragment",
        help: "Group or category name fragments never defunded in auto-funding operations",
      },
      {
        key: "protected_names",
        label: "Protected Categories",
        type: "validated-tags",
        suggestions: "categories",
        help: "Individual category names never defunded",
      },
      {
        key: "excluded_groups",
        label: "Excluded Groups",
        type: "validated-tags",
        suggestions: "groups",
        recommendationKey: "excluded_groups",
        dividerBefore: "Exclusion",
        help: "Groups excluded from analysis, calibration, and budget reports",
      },
      {
        key: "excluded_categories",
        label: "Excluded Categories",
        type: "validated-tags",
        suggestions: "categories",
        recommendationKey: "excluded_categories",
        groupCoveredBy: "excluded_groups",
        help: "Individual categories excluded from two-pot analysis regardless of group",
      },
      {
        key: "bonus_funded_groups",
        label: "Bonus-Funded Groups",
        type: "validated-tags",
        suggestions: ["groups", "categories"] as SuggestionPool[],
        matchType: "fragment",
        recommendationKey: "bonus_funded_groups",
        dividerBefore: "Bonus Allocation",
        help: "Group or category name fragments funded from bonus income only",
      },
      {
        key: "bonus_funded_categories",
        label: "Bonus-Funded Categories",
        type: "validated-tags",
        suggestions: "categories",
        recommendationKey: "bonus_funded_categories",
        groupCoveredBy: "bonus_funded_groups",
        help: "Individual categories funded from bonus only",
      },
    ],
  },
  {
    title: "Subscriptions",
    description: "Controls which YNAB categories and payees are included in subscription analytics.",
    fields: [
      {
        key: "subscription_categories",
        label: "Subscription Categories",
        type: "validated-tags",
        suggestions: "categories",
        recommendationKey: "subscription_categories",
        placeholder: "Subscriptions (Personal), Subscriptions (Business)",
        help: "YNAB category names scanned for recurring charges. Default includes only Subscriptions (Personal) and Subscriptions (Business).",
      },
      {
        key: "non_subscription_payees",
        label: "Excluded Payees",
        type: "validated-tags",
        suggestions: "payees",
        help: "Payees in subscription categories that are not recurring (one-time fees, platform costs). These are skipped entirely.",
      },
      {
        key: "payee_prefix_overrides",
        label: "Prefix Normalizations",
        type: "pair-editor",
        col1Label: "Payee prefix",
        col2Label: "Canonical name",
        help: "Normalize bank-mangled payee names by prefix match. Example: CANVA*|Canva. Default: CANVA*|Canva.",
      },
      {
        key: "payee_name_overrides",
        label: "Name Overrides",
        type: "pair-editor",
        col1Label: "Raw payee name",
        col2Label: "Canonical name",
        help: "Exact payee name remapping for payees where the bank import name is unavailable. The raw name is matched against the YNAB payee_name field.",
      },
    ],
  },
  {
    title: "Advanced",
    fields: [
      {
        key: "data_dir",
        label: "Data Directory",
        type: "text",
        placeholder: "~/.local/share/ynab-tools",
        help: "Override the default path for ynab.db and backups. Leave blank for the default.",
      },
      {
        key: "fidelity_accounts",
        label: "Fidelity Accounts",
        type: "fidelity-accounts",
        help: "Maps Fidelity account numbers to YNAB account names. Only needed if you use 'ynab import positions' to reconcile brokerage portfolios. Account numbers appear in Fidelity CSV exports.",
      },
    ],
  },
]

const NUMERIC_KEYS = new Set(["regular_pay", "bonus_threshold", "gross_salary", "gross_ote", "retirement_annual", "retirement_rate", "employer_match_pct"])

// ---- Validation logic -------------------------------------------------------

function getFormatError(key: string, value: string): string | null {
  if (!value.trim()) return null
  if (key === "plan_id" && !isUUID(value)) {
    return "Should be a UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
  }
  if (key === "birth_year" && !isValidYear(value)) {
    return "Should be a 4-digit year between 1920 and 2010"
  }
  if (NUMERIC_KEYS.has(key) && !isNumeric(value)) {
    return "Must be a number"
  }
  return null
}

// ---- Config health analysis -------------------------------------------------

type IssueSeverity = "error" | "warning" | "info"
interface ConfigIssue { message: string; severity: IssueSeverity }

const SEVERITY_BANNER: Record<IssueSeverity, string> = {
  error:   "rounded-md border border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/20 px-3 py-2 text-xs text-red-800 dark:text-red-300 flex items-start gap-1.5",
  warning: "rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 flex items-start gap-1.5",
  info:    "rounded-md border border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/20 px-3 py-2 text-xs text-blue-800 dark:text-blue-300 flex items-start gap-1.5",
}
const SEVERITY_DOT: Record<IssueSeverity, string> = {
  error:   "bg-red-500",
  warning: "bg-amber-500",
  info:    "bg-blue-400",
}
const SEVERITY_ORDER: IssueSeverity[] = ["error", "warning", "info"]

function topSeverity(issues: ConfigIssue[]): IssueSeverity {
  for (const sev of SEVERITY_ORDER) {
    if (issues.some((i) => i.severity === sev)) return sev
  }
  return "info"
}

function IssueIcon({ severity, className }: { severity: IssueSeverity; className?: string }) {
  if (severity === "error") return <XCircle className={className} />
  if (severity === "warning") return <AlertTriangle className={className} />
  return <Info className={className} />
}

function isGroupCovered(
  catName: string,
  groupConfigVal: string,
  categoryGroupMap: Record<string, string>,
): boolean {
  const parentGroup = categoryGroupMap[catName]
  if (!parentGroup) return false
  const configuredGroups = groupConfigVal.split(",").map((g) => g.trim()).filter(Boolean)
  const parentLower = parentGroup.toLowerCase()
  return configuredGroups.some((g) => parentLower.includes(g.toLowerCase()))
}

function computeConfigIssues(
  form: Record<string, string>,
  validationData?: AdminValidationData,
): Record<string, ConfigIssue[]> {
  const issues: Record<string, ConfigIssue[]> = {}
  function add(section: string, severity: IssueSeverity, message: string) {
    ;(issues[section] ??= []).push({ severity, message })
  }

  if (!form["access_token"]?.trim()) {
    add("YNAB Connection", "error", "Access token is required. Nothing will work without it.")
  }
  if (!form["plan_id"]?.trim()) {
    add("YNAB Connection", "error", "Budget ID is required. Use 'Select from YNAB' to populate.")
  }

  const hasPayees       = !!(form["paycheck_payees"]?.trim())
  const regularPay      = parseFloat(form["regular_pay"] ?? "") || 0
  const bonusThreshold  = parseFloat(form["bonus_threshold"] ?? "") || 0
  const grossSalary     = parseFloat(form["gross_salary"] ?? "") || 0
  const grossOte        = parseFloat(form["gross_ote"] ?? "") || 0
  const hasBonusFunded  = !!(form["bonus_funded_groups"]?.trim()) || !!(form["bonus_funded_categories"]?.trim())

  if (!hasPayees && (regularPay > 0 || bonusThreshold > 0)) {
    add("Income", "warning", "Paycheck Payees not set - income detection and paycheck reports won't work correctly.")
  }
  if (hasPayees && regularPay === 0) {
    add("Income", "info", "Paycheck payees are configured. Click 'Calculate from transactions' to derive regular pay automatically.")
  }
  if (grossSalary > 0 && grossOte > 0 && grossOte < grossSalary) {
    add("Income", "warning", "Gross OTE (On-Target Earnings) is less than base salary. OTE should be greater than or equal to base salary.")
  }
  if (bonusThreshold > 0 && !hasBonusFunded) {
    add("Income", "warning", "Bonus threshold is set but no bonus-funded categories are configured - the threshold has no effect.")
  }

  const anyRatioSet = !!(form["ratio_housing"]?.trim()) || !!(form["ratio_auto"]?.trim()) || !!(form["ratio_debt"]?.trim())
  if (anyRatioSet && grossSalary === 0) {
    add("Health Ratios", "warning", "Gross Base Salary (Income section) must be set for health ratio calculations to work.")
  }
  if (grossSalary > 0 && !anyRatioSet) {
    add("Health Ratios", "warning", "Salary is configured but no ratio categories are set - budget health ratios will show N/A.")
  }

  const retirementAnnual = parseFloat(form["retirement_annual"] ?? "") || 0
  const employerMatch    = parseFloat(form["employer_match_pct"] ?? "") || 0
  const has401k          = !!(form["retirement_401k"]?.trim())
  const anyAccount       = has401k || !!(form["retirement_roth_ira"]?.trim()) || !!(form["retirement_trad_ira"]?.trim()) || !!(form["retirement_taxable"]?.trim())

  if (retirementAnnual > 0 && !anyAccount) {
    add("Retirement", "warning", "Annual contribution is set but no retirement accounts are configured - projections won't work.")
  }
  if (employerMatch > 0 && !has401k) {
    add("Retirement", "warning", "Employer match is configured but no 401k account is set.")
  }
  if (!!(form["birth_year"]?.trim()) && !(form["retirement_milestones"]?.trim())) {
    add("Retirement", "info", "Consider adding retirement milestones. Use the 'Suggested' chips to add standard ones.")
  }

  const splitRaw = (key: string) =>
    (form[key] ?? "").split(",").map((t) => t.trim()).filter(Boolean)
  const overlapOriginalCase = (aRaw: string[], bRaw: string[]): string[] => {
    const bLower = new Set(bRaw.map((t) => t.toLowerCase()))
    return aRaw.filter((t) => bLower.has(t.toLowerCase()))
  }

  const protectedGroupsRaw = splitRaw("protected_groups")
  const excludedGroupsRaw  = splitRaw("excluded_groups")
  const groupOverlap       = overlapOriginalCase(protectedGroupsRaw, excludedGroupsRaw)
  if (groupOverlap.length > 0) {
    add("Budget Structure", "warning", `Overlap between Protected Groups and Excluded Groups: ${groupOverlap.join(", ")}. A group can't be both.`)
  }

  const protectedNamesRaw  = splitRaw("protected_names")
  const bonusFundedCatsRaw = splitRaw("bonus_funded_categories")
  const catOverlap         = overlapOriginalCase(protectedNamesRaw, bonusFundedCatsRaw)
  if (catOverlap.length > 0) {
    add("Budget Structure", "warning", `Some categories appear in both Protected and Bonus-Funded: ${catOverlap.join(", ")}. This may cause funding conflicts.`)
  }

  const excludedCatsRaw    = splitRaw("excluded_categories")
  const excludedCatOverlap = overlapOriginalCase(protectedNamesRaw, excludedCatsRaw)
  if (excludedCatOverlap.length > 0) {
    add("Budget Structure", "warning", `Some categories appear in both Protected and Excluded: ${excludedCatOverlap.join(", ")}. A category cannot be both.`)
  }

  const cgMap = validationData?.category_group_map ?? {}
  const bonusCatsCovered = bonusFundedCatsRaw.filter((cat) =>
    isGroupCovered(cat, form["bonus_funded_groups"] ?? "", cgMap)
  )
  if (bonusCatsCovered.length > 0) {
    add("Budget Structure", "warning", `These bonus_funded_categories entries are already covered by their group in bonus_funded_groups: ${bonusCatsCovered.join(", ")}. Remove them to avoid redundancy.`)
  }

  const bonusCoveredExcludedCats = excludedCatsRaw.filter((cat) =>
    isGroupCovered(cat, form["excluded_groups"] ?? "", cgMap)
  )
  if (bonusCoveredExcludedCats.length > 0) {
    add("Budget Structure", "warning", `These excluded_categories entries are already covered by their group in excluded_groups: ${bonusCoveredExcludedCats.join(", ")}. Remove them to avoid redundancy.`)
  }

  return issues
}

// ---- DeriveIncomePreviewDialog ----------------------------------------------

function DeriveIncomePreviewDialog({
  result,
  onApply,
  onClose,
}: {
  result: DeriveIncomeResult | null
  onApply: (regularPay: number, bonusThreshold: number) => void
  onClose: () => void
}) {
  if (!result) return null

  return (
    <DialogRoot open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Calculated Income Values</DialogTitle>
          <DialogDescription>
            Derived from <span className="font-medium text-foreground">{result.payee}</span> ({result.transaction_count} paychecks, {result.frequency}). Review before applying - outliers in transaction history may affect accuracy.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="flex justify-between items-center py-2 border-b border-border">
            <span className="text-muted-foreground">Regular Paycheck (net)</span>
            <span className="font-semibold tabular-nums">${result.regular_pay.toLocaleString()}</span>
          </div>
          <div className="flex justify-between items-center py-2 border-b border-border">
            <span className="text-muted-foreground">Bonus Threshold</span>
            <span className="font-semibold tabular-nums">${result.bonus_threshold.toLocaleString()}</span>
          </div>
        </div>
        <DialogFooter>
          <DialogClose>
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          </DialogClose>
          <Button
            size="sm"
            onClick={() => {
              onApply(result.regular_pay, result.bonus_threshold)
              onClose()
            }}
          >
            Apply Both
          </Button>
        </DialogFooter>
      </DialogContent>
    </DialogRoot>
  )
}

// ---- Main component ---------------------------------------------------------

export function Admin() {
  const { data, isLoading, error: fetchError } = useAdminConfig()
  const { data: validationData } = useAdminValidation()
  const { mutate: updateConfig, isPending: isSaving } = useUpdateConfig()
  const { mutate: deriveIncome, isPending: isDeriving, error: deriveError, reset: resetDerive } = useDeriveIncome()

  const [form, setForm] = useState<Record<string, string>>({})
  const [dirty, setDirty] = useState(false)
  const [pendingPreview, setPendingPreview] = useState<DeriveIncomeResult | null>(null)
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set())

  function toggleSection(title: string) {
    setCollapsedSections((prev) => {
      const next = new Set(prev)
      if (next.has(title)) next.delete(title)
      else next.add(title)
      return next
    })
  }

  const numericSaveKeys = useMemo(() => new Set([...NUMERIC_KEYS, "birth_year"]), [])

  useEffect(() => {
    if (data) {
      const initial: Record<string, string> = {}
      for (const group of FIELD_GROUPS) {
        for (const field of group.fields) {
          const raw = (data.config as Record<string, unknown>)[field.key]
          initial[field.key] = numericSaveKeys.has(field.key)
            ? normalizeNumericStr(raw)
            : raw != null ? String(raw) : ""
        }
      }
      setForm(initial)
      setDirty(false)
    }
  }, [data, numericSaveKeys])

  function set(key: string, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setDirty(true)
    if (key === "paycheck_payees") {
      setPendingPreview(null)
      resetDerive()
    }
  }

  function handleDeriveIncome() {
    const payees = form["paycheck_payees"] ?? ""
    deriveIncome(
      { paycheck_payees: payees },
      {
        onSuccess: (result) => {
          setPendingPreview(result)
        },
      }
    )
  }

  function applyPreview(regularPay: number, bonusThreshold: number) {
    set("regular_pay", String(regularPay))
    set("bonus_threshold", String(bonusThreshold))
    toast.success("Income values applied.")
  }

  function handleReset() {
    if (!data) return
    const initial: Record<string, string> = {}
    for (const group of FIELD_GROUPS) {
      for (const field of group.fields) {
        const raw = (data.config as Record<string, unknown>)[field.key]
        initial[field.key] = numericSaveKeys.has(field.key)
          ? normalizeNumericStr(raw)
          : raw != null ? String(raw) : ""
      }
    }
    setForm(initial)
    setDirty(false)
  }

  function handleSave() {
    const merged: Record<string, unknown> = { ...(data?.config ?? {}) }
    for (const [key, val] of Object.entries(form)) {
      if (val === "") {
        delete merged[key]
      } else if (numericSaveKeys.has(key)) {
        const n = Number(val)
        merged[key] = isNaN(n) ? val : n
      } else {
        merged[key] = val
      }
    }
    updateConfig(merged, {
      onSuccess: () => {
        toast.success("Config saved.")
        setDirty(false)
      },
      onError: (err) => toast.error(`Save failed: ${err.message}`),
    })
  }

  const [bannerExpanded, setBannerExpanded] = useState(false)

  const warnings = useMemo(() => {
    if (!validationData) return []
    const result: { label: string; bad: string[]; poolLabel: string }[] = []
    for (const group of FIELD_GROUPS) {
      for (const field of group.fields) {
        if (!field.suggestions) continue
        const pool = resolvePool(field.suggestions, validationData)
        if (!pool.length) continue
        const val = form[field.key] ?? ""
        if (!val.trim()) continue
        if (field.type === "validated-tags") {
          const bad = val.split(",").map((t) => t.trim()).filter((t) => t && !isTagKnown(t, pool, field.matchType ?? "exact"))
          if (bad.length) result.push({ label: field.label, bad, poolLabel: resolvePoolLabel(field.suggestions) })
        } else if (field.type === "validated-text") {
          if (!matchesSuggestion(val, pool)) result.push({ label: field.label, bad: [val.trim()], poolLabel: resolvePoolLabel(field.suggestions) })
        }
      }
    }
    return result
  }, [form, validationData])

  const configIssues = useMemo(() => computeConfigIssues(form, validationData), [form, validationData])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-muted-foreground">
        Loading config...
      </div>
    )
  }

  if (fetchError || !data) {
    return (
      <Alert variant="destructive">
        <h5 className="mb-1 font-medium leading-none tracking-tight">Failed to load config</h5>
        <div className="text-sm">Make sure the API server is running.</div>
      </Alert>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Admin</h2>
          <p className="text-muted-foreground text-sm">{data.path}</p>
        </div>
        <div className="flex gap-2 shrink-0 pt-1">
          {dirty && (
            <Button variant="outline" size="sm" onClick={handleReset} disabled={isSaving}>
              Reset
            </Button>
          )}
          <Button size="sm" onClick={handleSave} disabled={isSaving || !dirty}>
            {isSaving ? "Saving..." : "Save"}
          </Button>
        </div>
      </div>

      {!data.exists && (
        <div className="rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          Config file does not exist yet. Saving will create it.
        </div>
      )}

      {warnings.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 text-xs text-amber-800 dark:text-amber-300">
          <button
            onClick={() => setBannerExpanded(!bannerExpanded)}
            className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left"
          >
            <span className="flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {warnings.length} field{warnings.length > 1 ? "s contain values" : " contains a value"} not found in your YNAB data. Click to see which.
            </span>
            <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${bannerExpanded ? "rotate-180" : ""}`} />
          </button>
          {bannerExpanded && (
            <div className="px-3 pb-2.5 pt-2 space-y-1.5 border-t border-amber-200 dark:border-amber-800">
              {warnings.map((w) => (
                <div key={w.label}>
                  <span className="font-medium">{w.label}:</span>{" "}
                  {w.bad.map((b, i) => (
                    <span key={i} className="font-mono bg-amber-100 dark:bg-amber-900/40 rounded px-1 mr-1">{b}</span>
                  ))}
                  <span className="text-amber-600 dark:text-amber-500">not found in {w.poolLabel}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {pendingPreview && (
        <DeriveIncomePreviewDialog
          result={pendingPreview}
          onApply={applyPreview}
          onClose={() => { setPendingPreview(null); resetDerive() }}
        />
      )}

      {FIELD_GROUPS.map((group) => {
        const isCollapsed = collapsedSections.has(group.title)
        return (
        <Card key={group.title}>
          <CardHeader className={isCollapsed ? "" : "pb-3"}>
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-base flex items-center gap-2">
                {group.title}
                {(configIssues[group.title] ?? []).length > 0 && (
                  <span className={`w-2 h-2 rounded-full shrink-0 ${SEVERITY_DOT[topSeverity(configIssues[group.title])]}`} />
                )}
              </CardTitle>
              <div className="flex items-center gap-2 shrink-0">
                {group.title === "Income" && (
                  <button
                    type="button"
                    onClick={handleDeriveIncome}
                    disabled={isDeriving || !(form["paycheck_payees"] ?? "").trim()}
                    title={!(form["paycheck_payees"] ?? "").trim() ? "Set Paycheck Payees first" : undefined}
                    className="text-xs text-primary hover:underline underline-offset-2 disabled:opacity-40 disabled:no-underline disabled:cursor-not-allowed"
                  >
                    {isDeriving ? "Calculating..." : "Calculate from transactions"}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => toggleSection(group.title)}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label={isCollapsed ? "Expand section" : "Collapse section"}
                >
                  <ChevronDown className={`h-4 w-4 transition-transform ${isCollapsed ? "" : "rotate-180"}`} />
                </button>
              </div>
            </div>
            {!isCollapsed && group.description && (
              <p className="text-xs text-muted-foreground">{group.description}</p>
            )}
          </CardHeader>
          {!isCollapsed && (
          <CardContent className="space-y-4">
            {(configIssues[group.title] ?? []).map((issue, i) => (
              <div key={i} className={SEVERITY_BANNER[issue.severity]}>
                <IssueIcon severity={issue.severity} className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <span>{issue.message}</span>
              </div>
            ))}
            {group.title === "Income" && deriveError && (
              <p className="text-xs text-destructive">{deriveError.message}</p>
            )}
            {group.fields.map((field) => {
              const val = form[field.key] ?? ""
              const formatError = getFormatError(field.key, val)
              const pool = resolvePool(field.suggestions, validationData)

              const fieldRecs = field.recommendationKey
                ? (validationData?.recommendations?.[field.recommendationKey] ?? [])
                : []
              const currentTagsForRec = val.split(",").map(t => t.trim()).filter(Boolean)
              const pendingRecs = fieldRecs.filter(r =>
                field.type === "validated-text"
                  ? val.trim().toLowerCase() !== r.toLowerCase()
                  : !currentTagsForRec.some(t => t.toLowerCase() === r.toLowerCase())
              ).filter(r =>
                !field.groupCoveredBy ||
                !isGroupCovered(r, form[field.groupCoveredBy] ?? "", validationData?.category_group_map ?? {})
              )

              return (
                <Fragment key={field.key}>
                  {field.dividerBefore && (
                    <div className="flex items-center gap-2 pt-1">
                      <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                        {field.dividerBefore}
                      </span>
                      <div className="flex-1 h-px bg-border" />
                    </div>
                  )}
                <FormField
                  label={field.label}
                  help={field.help}
                  error={formatError}
                  required={field.required}
                >
                  <>
                  {field.readonly ? (
                    <div className="space-y-1">
                      <p className="text-sm text-muted-foreground py-1 font-mono">
                        {val || <span className="italic font-sans">Not set</span>}
                      </p>
                      {field.key === "plan_id" && (
                        <BudgetPicker
                          token={form["access_token"] ?? ""}
                          onSelect={(id, name) => {
                            set("plan_id", id)
                            set("plan_name", name)
                          }}
                        />
                      )}
                    </div>
                  ) : field.type === "password" ? (
                    <PasswordInput
                      value={val}
                      onChange={(v) => set(field.key, v)}
                      placeholder={field.placeholder}
                    />
                  ) : field.type === "validated-tags" ? (
                    <ValidatedTagInput
                      value={val}
                      onChange={(v) => set(field.key, v)}
                      suggestions={pool}
                      placeholder={field.placeholder}
                      matchType={field.matchType ?? "exact"}
                    />
                  ) : field.type === "tags" ? (
                    <ValidatedTagInput
                      value={val}
                      onChange={(v) => set(field.key, v)}
                      suggestions={[]}
                      placeholder={field.placeholder}
                    />
                  ) : field.type === "validated-text" ? (
                    <ValidatedTextInput
                      value={val}
                      onChange={(v) => set(field.key, v)}
                      suggestions={pool}
                      fieldId={field.key}
                      placeholder={field.placeholder}
                    />
                  ) : field.type === "milestone-list" ? (
                    <MilestoneEditor
                      value={val}
                      onChange={(v) => set(field.key, v)}
                      birthYear={parseInt(form["birth_year"] ?? "") || undefined}
                    />
                  ) : field.type === "fidelity-accounts" ? (
                    <FidelityAccountEditor
                      value={val}
                      onChange={(v) => set(field.key, v)}
                      accounts={validationData?.accounts ?? []}
                    />
                  ) : field.type === "pair-editor" ? (
                    <PairEditor
                      value={val}
                      onChange={(v) => set(field.key, v)}
                      col1Label={field.col1Label ?? "Raw"}
                      col2Label={field.col2Label ?? "Canonical"}
                      col1Suggestions={field.key === "payee_name_overrides" ? validationData?.payees : undefined}
                    />
                  ) : field.type === "textarea" ? (
                    <textarea
                      value={val}
                      onChange={(e) => set(field.key, e.target.value)}
                      placeholder={field.placeholder}
                      rows={4}
                      spellCheck={false}
                      className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-none focus:outline-none focus:ring-1 focus:ring-ring"
                    />
                  ) : (
                    <div className={(field.prefix || field.suffix) ? "relative flex items-center" : undefined}>
                      {field.prefix && (
                        <span className="absolute left-3 text-sm text-muted-foreground pointer-events-none select-none">
                          {field.prefix}
                        </span>
                      )}
                      <input
                        type="text"
                        inputMode={field.type === "number" ? "decimal" : undefined}
                        value={val}
                        onChange={(e) => set(field.key, e.target.value)}
                        placeholder={field.placeholder}
                        className={`w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring${field.prefix ? " pl-6" : ""}${field.suffix ? " pr-8" : ""}`}
                      />
                      {field.suffix && (
                        <span className="absolute right-3 text-sm text-muted-foreground pointer-events-none select-none">
                          {field.suffix}
                        </span>
                      )}
                    </div>
                  )}
                  {pendingRecs.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1 mt-1.5">
                      <span className="text-xs text-muted-foreground shrink-0">Suggested:</span>
                      {pendingRecs.map(r => (
                        <button
                          key={r}
                          type="button"
                          onClick={() => {
                            if (field.type === "validated-text") {
                              set(field.key, r)
                            } else {
                              const tags = (form[field.key] ?? "").split(",").map(t => t.trim()).filter(Boolean)
                              if (!tags.some(t => t.toLowerCase() === r.toLowerCase())) {
                                set(field.key, [...tags, r].join(", "))
                              }
                            }
                          }}
                          className="text-xs px-2 py-0.5 rounded-full border border-dashed border-input bg-background hover:bg-muted transition-colors"
                        >
                          {r}
                        </button>
                      ))}
                    </div>
                  )}
                  </>
                </FormField>
                </Fragment>
              )
            })}
          </CardContent>
          )}
        </Card>
        )
      })}
    </div>
  )
}
