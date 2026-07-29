import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

export interface CategoryData {
  group: string
  name: string
  budgeted: number
  spent: number
  balance: number
  pct_used: number
  pace_ratio: number
  status: "ON_TRACK" | "RUNNING_HOT" | "OVERSPENT" | "UNDERSPENT"
}

export interface RunningHot {
  name: string
  group: string
  budgeted: number
  spent: number
  pct_used: number
  pace_ratio: number
  projected: number
  status: "RUNNING_HOT" | "OVERSPENT"
}

export interface OverviewPriorities {
  overspent_count: number
  running_hot_count: number
  all_clear: boolean
}

export interface OverviewData {
  month: string
  rta: number
  income: number
  spending: number
  net: number
  days_elapsed: number
  days_in_month: number
  pct_elapsed: number
  running_hot: RunningHot[]
  categories: CategoryData[]
  priorities: OverviewPriorities
  biggest_overspend_z_score?: number | null
  biggest_overspend_anomaly_likely_one_time?: boolean
  biggest_overspend_name?: string | null
}

export interface MonthSummary {
  month: string
  income: number
  spending: number
  net: number
  surplus: boolean
}

export interface SummaryData {
  months: MonthSummary[]
  count: number
}

export interface MonthsData {
  months: string[]
  count: number
}

export interface TrendMonth {
  month: string
  income: number
  spending: number
  net: number
  surplus: boolean
  rolling_avg_spending: number
}

export interface TrendsData {
  months: TrendMonth[]
  streak: { type: "surplus" | "deficit"; count: number }
  best_month: { month: string; net: number }
  worst_month: { month: string; net: number }
  avg_monthly_income: number
  gross_monthly_income: number | null
  avg_monthly_spending: number
  insight: string
}

export interface GroupTrendData {
  months: string[]
  groups: { group: string; totals: number[] }[]
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((err as { detail?: string }).detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

async function patchJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((err as { detail?: string }).detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

export function useOverview(month?: string) {
  const params = month ? `?month=${month}` : ""
  return useQuery<OverviewData>({
    queryKey: ["overview", month ?? "current"],
    queryFn: () => fetchJson(`/api/overview${params}`),
    staleTime: 60_000,
  })
}

export function useMonths() {
  return useQuery<MonthsData>({
    queryKey: ["months"],
    queryFn: () => fetchJson("/api/months"),
    staleTime: 300_000,
  })
}

export function useSummary(months = 6) {
  return useQuery<SummaryData>({
    queryKey: ["summary", months],
    queryFn: () => fetchJson(`/api/summary?months=${months}`),
    staleTime: 60_000,
  })
}

export function formatCurrency(value: number, compact = false): string {
  if (compact && Math.abs(value) >= 1000) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value)
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value)
}

export function useTrends(months = 12) {
  return useQuery<TrendsData>({
    queryKey: ["trends", months],
    queryFn: () => fetchJson(`/api/trends?months=${months}`),
    staleTime: 60_000,
  })
}

export function useGroupTrend(months = 6) {
  return useQuery<GroupTrendData>({
    queryKey: ["group-trend", months],
    queryFn: () => fetchJson(`/api/group-trend?months=${months}`),
    staleTime: 60_000,
  })
}

export type FilterOption = "All" | "Over Target" | "Under Target" | "On Target" | "Unbudgeted"

// "budget-fit", "trends", and "overspend-plan" are kept as redirect source keys in PAGE_REDIRECTS even though they have no nav entries.
export type Page = "overview" | "budget-fit" | "trends" | "calibration" | "sinking-funds" | "report" | "upcoming" | "audit" | "churn" | "two-pot" | "admin" | "retirement" | "subscriptions" | "spending-pace" | "paycheck-funding" | "income" | "unapproved" | "month-end" | "overspend-plan" | "financial-health" | "net-worth"

export interface CalibrationCategory {
  id: string | null
  group: string
  name: string
  current_target: number
  median_monthly_spend: number
  avg_monthly_spend: number
  recommended_target: number
  variance_pct: number | null
  months_active: number
  months_over_target: number
  status: "OVER_TARGET" | "UNDER_TARGET" | "ON_TARGET" | "UNBUDGETED"
  spending_pattern: "consistent" | "moderate_variance" | "lumpy" | "high_variance"
  cv: number
  goal_type: string | null
  goal_target_month: string | null
  budget_month: string
  recent_anomaly_months: number
  recommendation_confidence: "high" | "moderate" | "low" | null
  anomaly_supports_raise: boolean | null
}

export interface CalibrationData {
  months_analyzed: number
  current_month: string
  categories: CalibrationCategory[]
  over_target_count: number
  under_target_count: number
  on_target_count: number
  unbudgeted_count: number
  potential_savings: number
  required_additions: number
  net_headroom_impact: number
}

export function useCalibration(months = 12) {
  return useQuery<CalibrationData>({
    queryKey: ["calibration", months],
    queryFn: () => fetchJson(`/api/calibration?months=${months}`),
    staleTime: 300_000,
  })
}

export interface SinkingFundCategory {
  name: string
  group: string
  goal_type: string
  goal_type_label: string
  goal_target: number | null
  goal_target_month: string | null
  goal_cadence: number | null
  is_recurring: boolean
  next_due_date: string | null
  is_stale: boolean
  balance: number
  budgeted: number
  goal_under_funded: number
  status: "FUNDED" | "UNDERFUNDED" | "NEGATIVE"
  consistently_underfunded: boolean
}

export interface SinkingFundsSummary {
  funded_count: number
  underfunded_count: number
  negative_count: number
  total_needed: number
  total_balance: number
}

export interface SinkingFundsData {
  month: string
  categories: SinkingFundCategory[]
  summary: SinkingFundsSummary
}

export interface BudgetFitGroup {
  name: string
  total_target: number
  pct_of_income: number
  category_count: number
  over_target_count: number
  under_target_count: number
}

export interface BudgetFitCalibrationSummary {
  potential_savings: number
  required_additions: number
  net_headroom_change: number
  recommended_total: number
  recommended_headroom: number
  over_target_count: number
  under_target_count: number
  unbudgeted_avg_spend: number
}

export interface BudgetFitData {
  avg_monthly_income: number
  current_month_income: number
  analysis_months: number
  budget_month: string
  total_current_targets: number
  headroom: number
  pct_committed: number
  groups: BudgetFitGroup[]
  calibration_summary: BudgetFitCalibrationSummary
}

export function useBudgetFit(months = 12) {
  return useQuery<BudgetFitData>({
    queryKey: ["budget-fit", months],
    queryFn: () => fetchJson(`/api/budget-fit?months=${months}`),
    staleTime: 60_000,
  })
}

export interface SyncStatusData {
  last_synced_at: string | null
  last_auto_sync_at: string | null
  auto_sync_interval_minutes: number
}

export function useSyncStatus() {
  return useQuery<SyncStatusData>({
    queryKey: ["sync-status"],
    queryFn: () => fetchJson("/api/sync-status"),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
}

export function useSinkingFunds() {
  return useQuery<SinkingFundsData>({
    queryKey: ["sinking-funds"],
    queryFn: () => fetchJson("/api/sinking-funds"),
    staleTime: 60_000,
  })
}

export interface ReportRow {
  label: string
  total: number
  monthly_avg: number
}

export interface ReportData {
  group_by: string
  metric: string
  months: number
  rows: ReportRow[]
}

export function useReport(groupBy: string, metric: string, months: number) {
  return useQuery<ReportData>({
    queryKey: ["report", groupBy, metric, months],
    queryFn: () => fetchJson(`/api/report?group_by=${groupBy}&metric=${metric}&months=${months}`),
    staleTime: 300_000,
  })
}

export interface UpcomingItem {
  id?: number
  date: string
  label: string
  amount: number
  type: "planned" | "goal"
  group: string | null
  funded: boolean
  gap: number
  memo: string | null
}

export interface RecurringBill {
  payee_name: string
  expected_amount: number
  expected_day: number
  source: "recurring"
}

export interface UpcomingData {
  year: number
  month: number
  items: UpcomingItem[]
  recurring_bills: RecurringBill[]
  total_amount: number
  total_gap: number
  unfunded_count: number
}

export function useUpcoming(year: number, month: number) {
  return useQuery<UpcomingData>({
    queryKey: ["upcoming", year, month],
    queryFn: () => fetchJson(`/api/upcoming?year=${year}&month=${month}`),
    staleTime: 60_000,
  })
}

export function formatMonth(isoMonth: string): string {
  const [year, month] = isoMonth.split("-")
  const d = new Date(Number(year), Number(month) - 1, 1)
  return d.toLocaleString("en-US", { month: "short", year: "2-digit" })
}

export interface CategoryOption {
  id: string
  name: string
  group: string
}

export interface CategoriesData {
  categories: CategoryOption[]
}

export function useCategories() {
  return useQuery<CategoriesData>({
    queryKey: ["categories"],
    queryFn: () => fetchJson("/api/categories"),
    staleTime: 300_000,
  })
}

export interface FundGoalsPreview {
  name: string
  group: string
  current_budgeted: number
  new_budgeted: number
  amount: number
}

export interface FundGoalsResult {
  ok: boolean
  dry_run: boolean
  funded_count: number
  total_funded: number
  rta: number
  rta_insufficient: boolean
  categories: FundGoalsPreview[]
  skipped: string[]
  failures: { name: string; error: string }[]
}

export function useSyncNow() {
  const qc = useQueryClient()
  return useMutation<{ ok: boolean; synced_at: string }, Error>({
    mutationFn: () => postJson("/api/sync", {}),
    onSuccess: () => {
      qc.invalidateQueries()
    },
  })
}

export interface ApplyTargetPayload {
  category_id: string
  new_target: number
  category_name: string
  category_group: string
  goal_type: string
  goal_target_month: string | null
  budget_month: string
}

export function useApplyTarget() {
  const qc = useQueryClient()
  return useMutation<{ ok: boolean; new_target: number }, Error, ApplyTargetPayload>({
    mutationFn: ({ category_id, ...body }) =>
      patchJson(`/api/calibration/${category_id}/target`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["calibration"] })
      qc.invalidateQueries({ queryKey: ["budget-fit"] })
      qc.invalidateQueries({ queryKey: ["overview"] })
      qc.invalidateQueries({ queryKey: ["sinking-funds"] })
    },
  })
}

export interface FundGoalsPayload {
  budget_month: string
  dry_run: boolean
}

export function useFundGoals() {
  const qc = useQueryClient()
  return useMutation<FundGoalsResult, Error, FundGoalsPayload>({
    mutationFn: (body) => postJson("/api/sinking-funds/fund-goals", body),
    onSuccess: (_data, variables) => {
      if (!variables.dry_run) {
        qc.invalidateQueries({ queryKey: ["sinking-funds"] })
        qc.invalidateQueries({ queryKey: ["overview"] })
        qc.invalidateQueries({ queryKey: ["primary-action"] })
      }
    },
  })
}

export interface AddPlannedPayload {
  category_name: string
  category_id: string
  amount: number
  due_date: string
  memo: string
}

export function useAddPlanned() {
  const qc = useQueryClient()
  return useMutation<{ ok: boolean; id: number }, Error, AddPlannedPayload>({
    mutationFn: (body) => postJson("/api/planned-expenses", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["upcoming"] })
    },
  })
}

export interface AuditEntry {
  timestamp: string
  action: string
  name: string | null
  details: string | null
  source: string
  log_type: "audit" | "funding" | "move"
  delta?: number | null
}

export interface AuditData {
  entries: AuditEntry[]
  count: number
}

export function useAuditLog(limit = 100, source = "all") {
  return useQuery<AuditData>({
    queryKey: ["audit", limit, source],
    queryFn: () => fetchJson(`/api/audit?limit=${limit}&source=${source}`),
    staleTime: 30_000,
  })
}

export interface ChurnRow {
  category_name: string
  category_group: string | null
  total_moves: number
  times_added: number
  times_removed: number
  total_added: number
  total_removed: number
  net_change: number
  is_churning: boolean
  current_target: number | null
}

export interface ChurnData {
  days: number
  rows: ChurnRow[]
}

export function useChurnAnalysis(days = 90) {
  return useQuery<ChurnData>({
    queryKey: ["churn", days],
    queryFn: () => fetchJson(`/api/churn?days=${days}`),
    staleTime: 60_000,
  })
}

export interface VarianceRow {
  category_name: string
  category_group: string | null
  months_tracked: number
  avg_budgeted: number
  stddev: number
  cv_pct: number
  min_budgeted: number
  max_budgeted: number
  is_volatile: boolean
}

export interface VarianceData {
  months: number
  rows: VarianceRow[]
}

export function useVarianceAnalysis(months = 6) {
  return useQuery<VarianceData>({
    queryKey: ["variance", months],
    queryFn: () => fetchJson(`/api/variance?months=${months}`),
    staleTime: 60_000,
  })
}

export interface OverspendRow {
  category_name: string
  category_group: string | null
  months_tracked: number
  times_overspent: number
  overspend_rate: number
  avg_budgeted: number
  avg_spending: number
  avg_ratio: number
  is_overspent: boolean
}

export interface OverspendData {
  months: number
  rows: OverspendRow[]
}

export function useOverspendAnalysis(months = 6) {
  return useQuery<OverspendData>({
    queryKey: ["overspend", months],
    queryFn: () => fetchJson(`/api/overspend?months=${months}`),
    staleTime: 60_000,
  })
}

export interface TwoPotCategory {
  category_name: string
  group: string | null
  delta: number
}

export interface TwoPotStructuralGap {
  regular_pot_budgeted: number
  monthly_regular_pay: number
  headroom: number
}

export interface TwoPotMonth {
  month: string
  bonus_paycheck_date: string
  bonus_paycheck_amount: number
  bonus_portion: number
  holding_start: number | null
  holding_end: number | null
  holding_delta: number
  holding_delta_warning: boolean
  correct: TwoPotCategory[]
  backwards: TwoPotCategory[]
  total_correct: number
  workflow_gap: number
  structural_backwards: number
  structural_is_exact: boolean
  tbb: number | null  // to_be_budgeted; null if budget_months row missing; available for future display
  structural_gap: TwoPotStructuralGap
}

export interface TwoPotData {
  config_ok: boolean
  regular_pay: number | null
  bonus_threshold: number | null
  months: TwoPotMonth[]
}

export function useTwoPot() {
  return useQuery<TwoPotData>({
    queryKey: ["two-pot"],
    queryFn: () => fetchJson("/api/two-pot"),
    staleTime: 60_000,
  })
}

export interface NeedsAttentionItem {
  id: string
  date: string
  payee: string
  account: string
  category: string | null
  amount: number
  memo: string | null
}

export interface NeedsAttentionData {
  needs_category: NeedsAttentionItem[]
  ready_to_approve: NeedsAttentionItem[]
  needs_category_count: number
  ready_to_approve_count: number
  total: number
  uncategorized_overspent_dollars: number | null
}

export function useNeedsAttention() {
  return useQuery<NeedsAttentionData>({
    queryKey: ["needs-attention"],
    queryFn: () => fetchJson("/api/needs-attention"),
    staleTime: 30_000,
  })
}

export interface ApproveAllResult {
  ok: boolean
  approved_count: number
  failed_count: number
}

export function useApproveAll() {
  const qc = useQueryClient()
  return useMutation<ApproveAllResult, Error>({
    mutationFn: () => postJson("/api/approve-all", {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["needs-attention"] })
      qc.invalidateQueries({ queryKey: ["overview"] })
      qc.invalidateQueries({ queryKey: ["primary-action"] })
    },
  })
}

export function useMarkPlanDone() {
  const qc = useQueryClient()
  return useMutation<{ ok: boolean }, Error, number>({
    mutationFn: (id) => patchJson(`/api/planned-expenses/${id}/done`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["upcoming"] })
    },
  })
}

export interface AdminConfigData {
  config: Record<string, unknown>
  path: string
  exists: boolean
}

export function useAdminConfig() {
  return useQuery<AdminConfigData>({
    queryKey: ["admin-config"],
    queryFn: () => fetchJson("/api/admin/config"),
    staleTime: 0,
  })
}

export type ValidationRecommendations = {
  retirement_401k: string[]
  retirement_roth_ira: string[]
  retirement_trad_ira: string[]
  retirement_taxable: string[]
  ratio_housing: string[]
  ratio_auto: string[]
  ratio_debt: string[]
  excluded_groups: string[]
  excluded_categories: string[]
  bonus_funded_groups: string[]
  bonus_funded_categories: string[]
  subscription_categories: string[]
}

export interface AdminValidationData {
  accounts: string[]
  groups: string[]
  categories: string[]
  payees: string[]
  category_group_map: Record<string, string>
  recommendations?: ValidationRecommendations
}

export interface BudgetOption {
  id: string
  name: string
}

export function useFetchBudgets() {
  return useMutation<{ budgets: BudgetOption[] }, Error, { access_token?: string }>({
    mutationFn: (body) => postJson("/api/admin/budgets", body),
  })
}

export function useAdminValidation() {
  return useQuery<AdminValidationData>({
    queryKey: ["admin-validation"],
    queryFn: () => fetchJson("/api/admin/validation-data"),
    staleTime: 300_000,
  })
}

async function putJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error((err as { detail?: string }).detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

export interface DeriveIncomeResult {
  regular_pay: number
  bonus_threshold: number
  payee: string
  transaction_count: number
  frequency: string
}

export interface RetirementAccount {
  type: string
  label: string
  name: string
  balance: number
  stale: boolean
  days_stale: number | null
  last_updated: string | null
}

export interface ReadinessTarget {
  age: number
  multiplier: number
  amount: number
}

export interface RetirementReadiness {
  multiplier: number
  gross_salary: number
  current_target_multiplier: number
  current_target_amount: number
  status: "ahead" | "on_track" | "behind"
  gap: number
  targets: ReadinessTarget[]
}

export interface ContributionLimit {
  limit: number
  catch_up: number
  effective_limit: number
  contributed: number
  pct: number
}

export interface TotalContributionLimit {
  limit: number
  contributed: number
  pct: number
}

export interface RetirementContributions {
  year: number
  limit_year: number
  catch_up_eligible: boolean
  by_account: Record<string, Record<string, number>>
  employer_match_annual: number
  limits: {
    employee_401k: ContributionLimit
    total_401k: TotalContributionLimit
    ira: ContributionLimit
  }
  annual_pace: number
  pace_basis_year: number
}

export interface ProjectionPoint {
  year: number
  age: number | null
  balance: number
  milestone: string | null
}

export interface SSScenario {
  label: string
  age: number
  monthly_benefit: number
}

export interface SSCombined {
  fra_portfolio_monthly: number | null
  fra_ss_monthly: number
  fra_total_monthly: number | null
  delayed_portfolio_monthly: number | null
  delayed_ss_monthly: number | null
  delayed_total_monthly: number | null
}

export interface SocialSecurityData {
  configured: boolean
  statement_date: string | null
  scenarios: SSScenario[]
  combined: SSCombined | null
}

export interface RetirementData {
  configured: boolean
  current_age: number | null
  accounts: RetirementAccount[]
  total_invested: number
  readiness: RetirementReadiness | null
  contributions: RetirementContributions | null
  projection: ProjectionPoint[]
  return_rate_pct: number
  social_security: SocialSecurityData | null
}

export function useRetirement() {
  return useQuery<RetirementData>({
    queryKey: ["retirement"],
    queryFn: () => fetchJson("/api/retirement"),
    staleTime: 60_000,
  })
}

export function useDeriveIncome() {
  return useMutation<DeriveIncomeResult, Error, { paycheck_payees: string }>({
    mutationFn: (body) => postJson("/api/admin/derive-income", body),
  })
}

export function useUpdateConfig() {
  const qc = useQueryClient()
  return useMutation<{ ok: boolean; path: string }, Error, Record<string, unknown>>({
    mutationFn: (config) => putJson("/api/admin/config", { config }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-config"] })
    },
  })
}

export interface SubscriptionItem {
  payee: string
  category: string
  frequency: "monthly" | "quarterly" | "annual" | "irregular"
  median_amount: number
  last_charged: string
  next_expected: string | null
  monthly_cost: number
  annual_cost: number
  status: "active" | "check"
  transaction_count: number
  monthly_data: { month: string; total: number }[]
}

export interface SubscriptionData {
  subscriptions: SubscriptionItem[]
  monthly_total: number
  annual_total: number
  active_count: number
  check_count: number
  yoy_change: number | null
  categories_searched: string[]
  detection_months: number
  config_hint?: string
}

export function useSubscriptions() {
  return useQuery<SubscriptionData>({
    queryKey: ["subscriptions"],
    queryFn: () => fetchJson("/api/subscriptions"),
    staleTime: 5 * 60_000,
  })
}

// --- Spending Pace ---

export interface SpendingPaceCategory {
  name: string
  budgeted: number
  spent: number
  pct_used: number
  pace: number
  projected: number
  trailing_avg: number
  status: "hot" | "overspent" | "on_track" | "under"
  z_score: number | null
  anomaly_label: string | null
}

export interface SpendingPaceData {
  month: string
  days_elapsed: number
  days_in_month: number
  pct_elapsed: number
  categories: SpendingPaceCategory[]
  hot_count: number
  overspent_count: number
  on_track_count: number
  under_count: number
  projected_overspend: number
}

export function useSpendingPace() {
  return useQuery<SpendingPaceData>({
    queryKey: ["spending-pace"],
    queryFn: () => fetchJson("/api/spending-pace"),
    staleTime: 60_000,
  })
}

// --- Paycheck Funding ---

export interface PaycheckFundingItem {
  id: string
  name: string
  group: string | null
  budgeted: number
  balance: number
  amount_needed: number
  reason: string
}

export interface PaycheckFundingTier {
  tier: number
  label: string
  total: number
  items: PaycheckFundingItem[]
}

export interface NextIncome {
  date: string
  amount: number
  payee: string
  is_bonus: boolean
}

export interface PaycheckFundingData {
  month: string
  rta: number
  next_income: NextIncome | null
  tiers: PaycheckFundingTier[]
  total_needed: number
  protected_total: number
  is_protected_covered: boolean
  tier_descriptions: Record<number, string>
}

export interface ApplyFundingResult {
  ok: boolean
  funded: { name: string; funded_amount: number; partial: boolean }[]
  skipped: { name: string; amount_needed: number; error?: string }[]
  total_funded: number
}

export function usePaycheckFunding() {
  return useQuery<PaycheckFundingData>({
    queryKey: ["paycheck-funding"],
    queryFn: () => fetchJson("/api/paycheck-funding"),
    staleTime: 60_000,
  })
}

export function useApplyPaycheckFunding() {
  const qc = useQueryClient()
  return useMutation<ApplyFundingResult, Error, { through_tier: number }>({
    mutationFn: (body) => postJson("/api/paycheck-funding/apply", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["paycheck-funding"] })
      qc.invalidateQueries({ queryKey: ["overview"] })
      qc.invalidateQueries({ queryKey: ["primary-action"] })
    },
  })
}

// --- Income ---

export interface PaycheckEntry {
  date: string
  payee: string
  amount: number
  regular: number
  bonus: number
  is_bonus: boolean
}

export interface IncomeMonth {
  month: string
  regular: number
  bonus: number
  other: number
  total: number
  paychecks: PaycheckEntry[]
}

export interface IncomeData {
  months: IncomeMonth[]
  ytd_regular: number
  ytd_bonus: number
  ytd_other: number
  ytd_total: number
  has_bonus_config: boolean
  regular_pay: number | null
  bonus_threshold: number | null
  gross_salary: number | null
  gross_ote: number | null
  current_year: string
  avg_monthly_net: number | null
  avg_monthly_net_months: number
}

export function useIncome(months = 12) {
  return useQuery<IncomeData>({
    queryKey: ["income", months],
    queryFn: () => fetchJson(`/api/income?months=${months}`),
    staleTime: 60_000,
  })
}

// --- Account Health ---

export interface StaleAccount {
  name: string
  last_reconciled_date: string | null
  days_since: number | null
}

export interface CCUnderfunded {
  account_name: string
  balance_dollars: number
  payment_category_balance_dollars: number
  gap_dollars: number
}

export interface AccountHealthData {
  as_of: string
  stale_accounts: StaleAccount[]
  stale_count: number
  cc_underfunded: CCUnderfunded[]
  cc_underfunded_count: number
}

export function useAccountHealth() {
  return useQuery<AccountHealthData>({
    queryKey: ["account-health"],
    queryFn: () => fetchJson("/api/overview/account-health"),
    staleTime: 5 * 60_000,
  })
}

// --- Health Ratios ---

export interface HealthRatioEntry {
  label: string
  monthly_amount: number
  actual_pct: number
  actual_ote_pct: number | null
  guideline_pct: number
  direction: "below" | "above"
  status: "ok" | "warning" | "over" | "under"
}

export interface HealthRatiosData {
  configured: boolean
  gross_salary?: number
  gross_ote?: number | null
  monthly_base?: number
  monthly_ote?: number | null
  month?: string
  ratios?: HealthRatioEntry[]
}

export function useHealthRatios() {
  return useQuery<HealthRatiosData>({
    queryKey: ["health-ratios"],
    queryFn: () => fetchJson("/api/overview/health-ratios"),
    staleTime: 5 * 60_000,
  })
}

// --- Debt Trend ---

export interface DebtAccount {
  name: string
  type: string
  balance_dollars: number
}

export interface DebtTrendData {
  as_of: string
  total_debt_dollars: number
  prior_month_dollars: number | null
  delta_dollars: number | null
  direction: "decreasing" | "increasing" | "flat" | null
  accounts: DebtAccount[]
}

export function useDebtTrend() {
  return useQuery<DebtTrendData>({
    queryKey: ["debt-trend"],
    queryFn: () => fetchJson("/api/overview/debt-trend"),
    staleTime: 5 * 60_000,
  })
}

// --- Savings Progress ---

export interface SavingsProgressItem {
  name: string
  type: "balance_goal" | "annual_contribution" | "monthly_target"
  // balance_goal fields
  current_balance?: number
  target?: number
  // annual_contribution fields
  ytd_contributed?: number
  annual_limit?: number
  months_remaining?: number
  // monthly_target fields
  ytd_actual?: number
  ytd_target?: number
  monthly_goal?: number
  // common
  pct_complete: number
  monthly_needed?: number
  year_elapsed_pct: number
}

export interface SavingsProgressData {
  year: number
  year_elapsed_pct: number
  months_elapsed: number
  months_remaining: number
  items: SavingsProgressItem[]
}

export function useSavingsProgress() {
  return useQuery<SavingsProgressData>({
    queryKey: ["savings-progress"],
    queryFn: () => fetchJson("/api/overview/savings-progress"),
    staleTime: 5 * 60_000,
  })
}

// --- Home Spending ---

export interface HomeSpendingCategory {
  name: string
  spent: number
}

export interface HomeSpendingMonth {
  month: string
  total_dollars: number
}

export interface HomeSpendingData {
  current_month: {
    month: string
    total_dollars: number
    categories: HomeSpendingCategory[]
  }
  prior_months: HomeSpendingMonth[]
  three_month_avg: number
  categories_tracked: string[]
}

export function useHomeSpending() {
  return useQuery<HomeSpendingData>({
    queryKey: ["home-spending"],
    queryFn: () => fetchJson("/api/overview/home-spending"),
    staleTime: 5 * 60_000,
  })
}

// --- Net Worth Trend ---

export interface NetWorthSnapshot {
  month: string
  net_worth: number
}

export interface NetWorthTrendData {
  has_data: boolean
  current_net_worth: number | null
  snapshots: NetWorthSnapshot[]
  months_of_history: number
  delta_from_last: number | null
  delta_from_month: string | null
  as_of?: string
}

export function useNetWorthTrend() {
  return useQuery<NetWorthTrendData>({
    queryKey: ["net-worth-trend"],
    queryFn: () => fetchJson("/api/overview/net-worth-trend"),
    staleTime: 5 * 60_000,
  })
}

// --- Net Worth Full Page ---

export interface NetWorthHistoryPoint {
  month: string
  net_worth: number
  net_worth_ex_mortgage: number
}

export interface NetWorthAccountDetail {
  name: string
  type: string
  on_budget: boolean
  balance: number
}

export interface NetWorthBreakdown {
  investments: number
  liquid: number
  mortgage: number
  other_debt: number
  net_worth_ex_mortgage: number
  accounts: NetWorthAccountDetail[]
}

export interface NetWorthCurrent {
  net_worth: number
  net_worth_ex_mortgage: number
  total_assets: number
  total_debt: number
}

export interface NetWorthData {
  has_data: boolean
  as_of: string
  current: NetWorthCurrent | null
  history: NetWorthHistoryPoint[]
  mom_change: number | null
  mom_from_month: string | null
  yoy_change: number | null
  yoy_from_month: string | null
  breakdown: NetWorthBreakdown | null
}

export function useNetWorth() {
  return useQuery<NetWorthData>({
    queryKey: ["net-worth"],
    queryFn: () => fetchJson("/api/net-worth"),
    staleTime: 5 * 60_000,
  })
}

// --- Unapproved Inbox ---

export interface UnapprovedItem {
  id: string
  date: string
  payee: string
  account: string
  category: string | null
  amount: number
  memo: string | null
}

export interface UnapprovedData {
  needs_category: UnapprovedItem[]
  ready_to_approve: UnapprovedItem[]
  needs_category_count: number
  ready_to_approve_count: number
  total: number
}

export interface ApproveResult {
  ok: boolean
  approved_count: number
  failed_count: number
  already_approved?: boolean
}

export function useUnapproved() {
  return useQuery<UnapprovedData>({
    queryKey: ["unapproved"],
    queryFn: () => fetchJson("/api/unapproved"),
    staleTime: 30_000,
  })
}

export function useApproveTransaction() {
  const qc = useQueryClient()
  return useMutation<ApproveResult, Error, { transaction_id: string }>({
    mutationFn: (body) => postJson("/api/unapproved/approve", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["unapproved"] })
      qc.invalidateQueries({ queryKey: ["needs-attention"] })
      qc.invalidateQueries({ queryKey: ["overview"] })
    },
  })
}

export function useApproveAllUnapproved() {
  const qc = useQueryClient()
  return useMutation<ApproveResult, Error>({
    mutationFn: () => postJson("/api/unapproved/approve", { all_categorized: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["unapproved"] })
      qc.invalidateQueries({ queryKey: ["needs-attention"] })
      qc.invalidateQueries({ queryKey: ["overview"] })
      qc.invalidateQueries({ queryKey: ["primary-action"] })
    },
  })
}

// --- Month-End ---

export interface MonthEndIncome {
  income: number
  prior_income: number | null
  delta: number | null
}

export interface MonthEndSpending {
  rta: number
  income: number
  budgeted: number
  activity: number
  net: number
}

export interface MonthEndOverspentCategory {
  name: string
  group: string | null
  budgeted: number
  activity: number
  balance: number
  structural: boolean
  z_score: number | null
  anomaly_label: string | null
  coverage_suggestion: "holding" | "waterfall" | null
}

export interface MonthEndOverspent {
  count: number
  total_overspent: number
  categories: MonthEndOverspentCategory[]
}

export interface MonthEndAttention {
  uncategorized: number
  unapproved: number
  pending: number
}

export interface MonthEndCreditCard {
  name: string
  balance: number
  payment_budgeted: number
  payment_balance: number
}

export interface MonthEndPlannedExpense {
  id: number
  category_name: string
  amount: number
  due_date: string
  gap: number
  funded: boolean
  overdue: boolean
  memo: string | null
}

export interface MonthEndData {
  month: string
  target: string
  income: MonthEndIncome
  spending: MonthEndSpending
  overspent: MonthEndOverspent
  attention: MonthEndAttention
  credit_cards: MonthEndCreditCard[]
  credit_card_total_balance: number
  planned_expenses: MonthEndPlannedExpense[]
  planned_total_gap: number
}

export function useMonthEnd(month?: string) {
  const params = month ? `?month=${month}` : ""
  return useQuery<MonthEndData>({
    queryKey: ["month-end", month ?? "auto"],
    queryFn: () => fetchJson(`/api/month-end${params}`),
    staleTime: 60_000,
  })
}

// --- Overspend Plan ---

export interface OverspendPlanCategory {
  name: string
  balance: number
  budgeted: number
  activity: number
  overspent: number
  classification: "STRUCTURAL" | "SEASONAL" | "ONE-OFF" | "TIMING FLOAT"
  detail: string
  z_score: number | null
  coverage_suggestion: "holding" | "waterfall" | null
}

export interface OverspendDonor {
  name: string
  group: string
  available: number
  suggested_transfer: number
  is_sinking_fund: boolean
}

export interface ClassificationSummaryEntry {
  count: number
  total: number
}

export interface OverspendPlanData {
  month: string
  month_label: string
  overspent_count: number
  total_overspent: number
  total_coverable: number
  gap: number
  fully_coverable: boolean
  overspent_categories: OverspendPlanCategory[]
  one_time_overspends: OverspendPlanCategory[]
  donors: OverspendDonor[]
  classification_summary: Record<string, ClassificationSummaryEntry>
}

export function useOverspendPlan(month?: string) {
  const params = month ? `?month=${month}` : ""
  return useQuery<OverspendPlanData>({
    queryKey: ["overspend-plan", month ?? "current"],
    queryFn: () => fetchJson(`/api/overspend-plan${params}`),
    staleTime: 60_000,
  })
}

// --- Primary Action (refs #187) ---

export type PrimaryActionPriority = "unapproved" | "fund_rta" | "fund_goals" | "structural_overspend" | "all_good"

export interface PrimaryActionData {
  priority: PrimaryActionPriority
  message: string
  detail: string | null
  action_path: string | null
}

export function usePrimaryAction() {
  return useQuery<PrimaryActionData>({
    queryKey: ["primary-action"],
    queryFn: () => fetchJson("/api/overview/primary-action"),
    staleTime: 60_000,
  })
}

let initialBuildId: string | null = null
let hasShownUpdateToast = false

export interface OverviewBundleData {
  overview: OverviewData | null
  months: MonthsData | null
  budget_fit: BudgetFitData | null
  sinking_funds: SinkingFundsData | null
  upcoming: UpcomingData | null
  subscriptions: SubscriptionData | null
  trends: TrendsData | null
  calibration: CalibrationData | null
  retirement: RetirementData | null
  paycheck_funding: PaycheckFundingData | null
  income: IncomeData | null
  churn: ChurnData | null
  two_pot: TwoPotData | null
  net_worth_trend: NetWorthTrendData | null
  savings_progress: SavingsProgressData | null
  home_spending: HomeSpendingData | null
  account_health: AccountHealthData | null
  health_ratios: HealthRatiosData | null
  debt_trend: DebtTrendData | null
  needs_attention: NeedsAttentionData | null
}

export function useOverviewBundle(month?: string) {
  const params = month ? `?month=${month}` : ""
  return useQuery<OverviewBundleData>({
    queryKey: ["overview-bundle", month ?? "current"],
    queryFn: () => fetchJson(`/api/overview-bundle${params}`),
    staleTime: 60_000,
  })
}

export function useVersionCheck() {
  useQuery<{ build_id: string }>({
    queryKey: ["version"],
    queryFn: async () => {
      const data = await fetchJson<{ build_id: string }>("/api/version")
      if (initialBuildId === null) {
        initialBuildId = data.build_id
      } else if (data.build_id !== initialBuildId && !hasShownUpdateToast) {
        hasShownUpdateToast = true
        toast.info("A new version is available. Reload to update.", {
          action: { label: "Reload", onClick: () => window.location.reload() },
          duration: Infinity,
        })
      }
      return data
    },
    staleTime: 0,
    refetchInterval: 60_000,
  })
}
