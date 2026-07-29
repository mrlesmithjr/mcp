import { useState } from "react"
import { ChevronLeft, ChevronRight, Plus } from "lucide-react"
import { toast } from "sonner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useUpcoming, useMarkPlanDone, useAddPlanned, useCategories, formatCurrency } from "@/lib/api"
import type { UpcomingItem, RecurringBill } from "@/lib/api"
import { cn } from "@/lib/utils"

const DAY_HEADERS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
]

function prevMonth(year: number, month: number): [number, number] {
  if (month === 1) return [year - 1, 12]
  return [year, month - 1]
}

function nextMonth(year: number, month: number): [number, number] {
  if (month === 12) return [year + 1, 1]
  return [year, month + 1]
}

function itemChipClass(item: UpcomingItem, isOverdue: boolean): string {
  if (isOverdue) return "bg-red-100 text-red-800 border border-red-200"
  if (!item.funded) return "bg-amber-50 text-amber-800 border border-amber-200"
  return "bg-green-50 text-green-800 border border-green-200"
}

function TypeBadge({ type }: { type: UpcomingItem["type"] }) {
  if (type === "goal")
    return <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200 text-xs">Goal</Badge>
  return <Badge variant="outline" className="bg-slate-50 text-slate-600 border-slate-200 text-xs">Planned</Badge>
}

const TODAY = new Date()
const DEFAULT_FORM_DATE = `${TODAY.getFullYear()}-${String(TODAY.getMonth() + 1).padStart(2, "0")}-15`

export function Upcoming() {
  const [year, setYear] = useState(TODAY.getFullYear())
  const [month, setMonth] = useState(TODAY.getMonth() + 1)
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [doneDialog, setDoneDialog] = useState<{ open: boolean; item: UpcomingItem | null }>({ open: false, item: null })
  const [formCategoryId, setFormCategoryId] = useState("")
  const [formAmount, setFormAmount] = useState("")
  const [formDate, setFormDate] = useState(DEFAULT_FORM_DATE)
  const [formMemo, setFormMemo] = useState("")

  const { data, isLoading, error } = useUpcoming(year, month)
  const { mutate: markDone, isPending: isMarkingDone, variables: markingId } = useMarkPlanDone()
  const { mutate: addPlanned, isPending: isAdding } = useAddPlanned()
  const { data: catData } = useCategories()

  const categoryOptions = catData?.categories ?? []
  const selectedCat = categoryOptions.find((c) => c.id === formCategoryId)

  function handlePrev() {
    const [y, m] = prevMonth(year, month)
    setYear(y)
    setMonth(m)
  }

  function handleNext() {
    const [y, m] = nextMonth(year, month)
    setYear(y)
    setMonth(m)
  }

  function handleMarkDone(item: UpcomingItem) {
    if (!item.id) return
    setDoneDialog({ open: true, item })
  }

  function handleConfirmDone() {
    const item = doneDialog.item
    if (!item?.id) return
    markDone(item.id, {
      onSuccess: () => toast.success(`Marked "${item.label}" complete`),
      onError: (err) => toast.error(`Failed: ${err.message}`),
    })
    setDoneDialog({ open: false, item: null })
  }

  function resetForm() {
    setFormCategoryId("")
    setFormAmount("")
    setFormDate(DEFAULT_FORM_DATE)
    setFormMemo("")
  }

  function handleAddSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedCat || !formAmount || !formDate) return
    addPlanned(
      {
        category_id: selectedCat.id,
        category_name: selectedCat.name,
        amount: parseFloat(formAmount),
        due_date: formDate,
        memo: formMemo,
      },
      {
        onSuccess: () => {
          toast.success("Planned expense added")
          setAddDialogOpen(false)
          resetForm()
        },
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    )
  }

  // Build calendar grid data
  const firstDayOfMonth = new Date(year, month - 1, 1).getDay() // 0=Sun
  const daysInMonth = new Date(year, month, 0).getDate()

  // Group items by day-of-month
  const itemsByDay: Record<number, UpcomingItem[]> = {}
  if (data) {
    for (const item of data.items) {
      const d = parseInt(item.date.split("-")[2], 10)
      if (!itemsByDay[d]) itemsByDay[d] = []
      itemsByDay[d].push(item)
    }
  }

  // Group recurring bills by expected_day, excluding any whose payee name
  // matches an existing planned/goal item label (case-insensitive).
  const recurringByDay: Record<number, RecurringBill[]> = {}
  if (data?.recurring_bills) {
    const plannedLabelsLower = new Set(data.items.map((i) => i.label.toLowerCase()))
    for (const bill of data.recurring_bills) {
      if (plannedLabelsLower.has(bill.payee_name.toLowerCase())) continue
      const d = Math.min(bill.expected_day, daysInMonth)
      if (!recurringByDay[d]) recurringByDay[d] = []
      recurringByDay[d].push(bill)
    }
  }

  const todayStr = TODAY.toISOString().slice(0, 10)

  // Build grid cells: leading empty + day cells
  const totalCells = firstDayOfMonth + daysInMonth
  const trailingCells = (7 - (totalCells % 7)) % 7
  const cells: (number | null)[] = [
    ...Array(firstDayOfMonth).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
    ...Array(trailingCells).fill(null),
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Upcoming</h2>
          <p className="text-muted-foreground text-sm">Planned expenses and sinking fund deadlines</p>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={() => setAddDialogOpen(true)}>
            <Plus className="h-3.5 w-3.5 mr-1" />
            Add Expense
          </Button>
          <div className="flex items-center gap-1">
            <button
              onClick={handlePrev}
              className="p-1.5 rounded-md border border-input bg-background hover:bg-muted transition-colors"
              aria-label="Previous month"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-sm font-medium w-32 text-center">
              {MONTH_NAMES[month - 1]} {year}
            </span>
            <button
              onClick={handleNext}
              className="p-1.5 rounded-md border border-input bg-background hover:bg-muted transition-colors"
              aria-label="Next month"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Mark Done Confirmation Dialog */}
      <DialogRoot open={doneDialog.open} onOpenChange={(open) => !open && setDoneDialog({ open: false, item: null })}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark as Done</DialogTitle>
            {doneDialog.item && (
              <DialogDescription>
                Mark &quot;{doneDialog.item.label}&quot; as complete?
              </DialogDescription>
            )}
          </DialogHeader>
          <DialogFooter>
            <DialogClose>
              <Button variant="outline" size="sm">Cancel</Button>
            </DialogClose>
            <Button size="sm" onClick={handleConfirmDone} disabled={isMarkingDone}>
              {isMarkingDone ? "Marking..." : "Mark Done"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </DialogRoot>

      {/* Add Planned Expense Dialog */}
      <DialogRoot open={addDialogOpen} onOpenChange={(open) => { setAddDialogOpen(open); if (!open) resetForm() }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Planned Expense</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleAddSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Category</label>
              <select
                required
                value={formCategoryId}
                onChange={(e) => setFormCategoryId(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="">Select a category...</option>
                {categoryOptions.map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.group ? `${cat.group}: ` : ""}{cat.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Amount</label>
              <input
                required
                type="number"
                min="0.01"
                step="0.01"
                value={formAmount}
                onChange={(e) => setFormAmount(e.target.value)}
                placeholder="0.00"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Due Date</label>
              <input
                required
                type="date"
                value={formDate}
                onChange={(e) => setFormDate(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">
                Memo <span className="text-muted-foreground font-normal">(optional)</span>
              </label>
              <input
                type="text"
                value={formMemo}
                onChange={(e) => setFormMemo(e.target.value)}
                placeholder="e.g. Appointment next month"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <DialogFooter>
              <DialogClose>
                <Button type="button" variant="outline" size="sm">Cancel</Button>
              </DialogClose>
              <Button type="submit" size="sm" disabled={isAdding}>
                {isAdding ? "Adding..." : "Add"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </DialogRoot>

      {isLoading && (
        <div className="flex items-center justify-center h-64 text-muted-foreground">
          Loading upcoming items...
        </div>
      )}

      {!isLoading && error && (
        <Alert variant="destructive">
          <AlertTitle>Failed to load data</AlertTitle>
          <AlertDescription>
            Make sure the API server is running and data is synced (<code>ynab sync</code>).
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !error && data && (
        <div className={`rounded-lg border px-4 py-2.5 text-sm ${data.total_gap > 0 ? "border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/20 text-amber-800 dark:text-amber-300" : "border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-950/20 text-green-800 dark:text-green-300"}`}>
          {data.items.length === 0 ? (
            "No planned expenses or goal deadlines this month."
          ) : data.total_gap > 0 ? (
            <span>
              {data.items.length} {data.items.length === 1 ? "item" : "items"} totaling {formatCurrency(data.total_amount)} this month. {formatCurrency(data.total_gap)} unfunded across {data.unfunded_count} {data.unfunded_count === 1 ? "item" : "items"}.
            </span>
          ) : (
            <span>
              {data.items.length} {data.items.length === 1 ? "item" : "items"} totaling {formatCurrency(data.total_amount)} this month, all funded.
            </span>
          )}
        </div>
      )}

      {!isLoading && !error && (
        <>
          {/* Calendar grid */}
          <div className="border rounded-lg overflow-hidden">
            <div className="grid grid-cols-7 border-b">
              {DAY_HEADERS.map((d) => (
                <div key={d} className="py-2 text-center text-xs font-medium text-muted-foreground bg-muted/30">
                  {d}
                </div>
              ))}
            </div>
            <div className="grid grid-cols-7">
              {cells.map((day, idx) => {
                if (day === null) {
                  return (
                    <div
                      key={`empty-${idx}`}
                      className={cn(
                        "min-h-[80px] p-1.5 border-b border-r bg-muted/10",
                        (idx + 1) % 7 === 0 && "border-r-0",
                        idx >= cells.length - 7 && "border-b-0",
                      )}
                    />
                  )
                }

                const dateStr = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`
                const isToday = dateStr === todayStr
                const dayItems = itemsByDay[day] ?? []
                const dayRecurring = recurringByDay[day] ?? []

                return (
                  <div
                    key={day}
                    className={cn(
                      "min-h-[80px] p-1.5 border-b border-r",
                      (idx + 1) % 7 === 0 && "border-r-0",
                      idx >= cells.length - 7 && "border-b-0",
                      isToday && "bg-blue-50/40 dark:bg-blue-950/20",
                    )}
                  >
                    <div
                      className={cn(
                        "text-xs font-medium mb-1 w-5 h-5 flex items-center justify-center rounded-full",
                        isToday
                          ? "bg-foreground text-background"
                          : "text-muted-foreground",
                      )}
                    >
                      {day}
                    </div>
                    <div className="space-y-0.5">
                      {dayItems.map((item, i) => {
                        const isOverdue = dateStr < todayStr
                        return (
                          <div
                            key={i}
                            title={`${item.label}${item.memo ? `: ${item.memo}` : ""}${!item.funded ? ` (needs ${formatCurrency(item.gap)})` : ""}`}
                            className={cn(
                              "text-[10px] leading-tight px-1 py-0.5 rounded truncate",
                              itemChipClass(item, isOverdue),
                            )}
                          >
                            {item.label}
                          </div>
                        )
                      })}
                      {dayRecurring.map((bill, i) => (
                        <div
                          key={`r-${i}`}
                          title={`${bill.payee_name} · ${formatCurrency(bill.expected_amount)} (recurring)`}
                          className="text-[10px] leading-tight px-1 py-0.5 rounded truncate bg-slate-100 text-slate-600 border border-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700"
                        >
                          {bill.payee_name}
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Detail list */}
          {data && data.items.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Item</TableHead>
                  <TableHead>Group</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead className="text-right">Gap</TableHead>
                  <TableHead>Note</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.items.map((item, i) => {
                  const isOverdue = item.date < todayStr
                  const isThisMarkingDone = isMarkingDone && markingId === item.id
                  return (
                    <TableRow key={i}>
                      <TableCell className={cn("tabular-nums text-sm", isOverdue ? "text-red-600 font-medium" : "text-muted-foreground")}>
                        {item.date}
                      </TableCell>
                      <TableCell className="font-medium">{item.label}</TableCell>
                      <TableCell className="text-muted-foreground text-sm">{item.group ?? "-"}</TableCell>
                      <TableCell><TypeBadge type={item.type} /></TableCell>
                      <TableCell className="text-right tabular-nums">{formatCurrency(item.amount)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {item.gap > 0
                          ? <span className="text-amber-600">{formatCurrency(item.gap)}</span>
                          : <span className="text-green-600 text-xs">funded</span>}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">{item.memo ?? "-"}</TableCell>
                      <TableCell>
                        {item.type === "planned" && item.id ? (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={isThisMarkingDone}
                            onClick={() => handleMarkDone(item)}
                          >
                            {isThisMarkingDone ? "..." : "Done"}
                          </Button>
                        ) : (
                          <span />
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          ) : (
            <div className="rounded-lg border bg-muted/20 p-5 space-y-4">
              <p className="text-sm text-muted-foreground">
                No planned expenses or goal deadlines for {MONTH_NAMES[month - 1]} {year}.
              </p>
              <div className="space-y-3">
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Add via CLI</p>
                  <code className="block text-xs bg-background border rounded px-3 py-2 text-foreground">
                    ynab plan add "Category Name" 500 --by {year}-{String(month).padStart(2, "0")}-15 --memo "optional note"
                  </code>
                </div>
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Add via Claude</p>
                  <code className="block text-xs bg-background border rounded px-3 py-2 text-foreground">
                    Add a planned expense for Home Repair for $800 due {MONTH_NAMES[month - 1]} 15
                  </code>
                </div>
              </div>
            </div>
          )}

          {/* Recurring Bills section */}
          {data && data.recurring_bills && data.recurring_bills.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-muted-foreground">Recurring Bills</h3>
                <Badge variant="outline" className="bg-slate-50 text-slate-600 border-slate-200 text-xs">
                  inferred from history
                </Badge>
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Expected Day</TableHead>
                    <TableHead>Payee</TableHead>
                    <TableHead className="text-right">Typical Amount</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.recurring_bills.map((bill, i) => (
                    <TableRow key={i} className="text-muted-foreground">
                      <TableCell className="tabular-nums text-sm">
                        {year}-{String(month).padStart(2, "0")}-{String(Math.min(bill.expected_day, daysInMonth)).padStart(2, "0")}
                      </TableCell>
                      <TableCell className="font-medium text-foreground">{bill.payee_name}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatCurrency(bill.expected_amount)}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="bg-slate-50 text-slate-500 border-slate-200 text-xs">
                          recurring
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
