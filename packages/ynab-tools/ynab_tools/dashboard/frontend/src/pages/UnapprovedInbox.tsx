import { useState } from "react"
import { CheckCheck, Check } from "lucide-react"
import { toast } from "sonner"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  useUnapproved,
  useApproveTransaction,
  useApproveAllUnapproved,
  formatCurrency,
} from "@/lib/api"
import type { UnapprovedItem } from "@/lib/api"
import { cn } from "@/lib/utils"

function formatDate(iso: string): string {
  const [year, month, day] = iso.split("-")
  return new Date(Number(year), Number(month) - 1, Number(day)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  })
}

function AmountCell({ amount }: { amount: number }) {
  const isExpense = amount < 0
  return (
    <span
      className={cn(
        "tabular-nums font-medium",
        isExpense ? "text-red-600 dark:text-red-400" : "text-green-600 dark:text-green-400",
      )}
    >
      {isExpense ? "-" : "+"}
      {formatCurrency(Math.abs(amount))}
    </span>
  )
}

function TransactionTable({
  items,
  approvingId,
  onApprove,
  showApproveButton,
}: {
  items: UnapprovedItem[]
  approvingId: string | null
  onApprove: (id: string) => void
  showApproveButton: boolean
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Date</TableHead>
          <TableHead>Payee</TableHead>
          <TableHead className="hidden sm:table-cell">Account</TableHead>
          <TableHead>Category</TableHead>
          <TableHead className="text-right">Amount</TableHead>
          <TableHead className="hidden md:table-cell">Memo</TableHead>
          {showApproveButton && <TableHead className="w-24" />}
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.id}>
            <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
              {formatDate(item.date)}
            </TableCell>
            <TableCell className="font-medium">{item.payee || "(unknown)"}</TableCell>
            <TableCell className="hidden sm:table-cell text-sm text-muted-foreground">
              {item.account}
            </TableCell>
            <TableCell>
              {item.category ? (
                <span className="text-sm">{item.category}</span>
              ) : (
                <span className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                  uncategorized
                </span>
              )}
            </TableCell>
            <TableCell className="text-right">
              <AmountCell amount={item.amount} />
            </TableCell>
            <TableCell className="hidden md:table-cell text-xs text-muted-foreground max-w-[180px] truncate">
              {item.memo ?? ""}
            </TableCell>
            {showApproveButton && (
              <TableCell className="text-right">
                <button
                  onClick={() => onApprove(item.id)}
                  disabled={approvingId === item.id}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium border transition-colors",
                    approvingId === item.id
                      ? "opacity-50 cursor-not-allowed border-input bg-muted text-muted-foreground"
                      : "border-green-200 bg-green-50 text-green-700 hover:bg-green-100 dark:border-green-800 dark:bg-green-950/40 dark:text-green-400 dark:hover:bg-green-900/60",
                  )}
                >
                  <Check className="h-3 w-3" />
                  {approvingId === item.id ? "..." : "Approve"}
                </button>
              </TableCell>
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function UnapprovedInbox() {
  const { data, isLoading } = useUnapproved()
  const approveOne = useApproveTransaction()
  const approveAll = useApproveAllUnapproved()
  const [approvingId, setApprovingId] = useState<string | null>(null)

  if (isLoading || !data) {
    return (
      <div className="space-y-6">
        <h2 className="text-xl font-semibold">Unapproved Inbox</h2>
        <p className="text-muted-foreground text-sm">Loading...</p>
      </div>
    )
  }

  const { needs_category, ready_to_approve } = data

  function handleApproveOne(id: string) {
    setApprovingId(id)
    approveOne.mutate(
      { transaction_id: id },
      {
        onSuccess: (result) => {
          setApprovingId(null)
          if (result.already_approved) {
            toast.info("Transaction was already approved.")
          } else if (result.ok) {
            toast.success("Transaction approved.")
          } else {
            toast.error("Failed to approve transaction.")
          }
        },
        onError: (err) => {
          setApprovingId(null)
          toast.error(`Approve failed: ${err.message}`)
        },
      },
    )
  }

  function handleApproveAll() {
    approveAll.mutate(undefined, {
      onSuccess: (result) => {
        if (result.approved_count === 0) {
          toast.info("No categorized transactions to approve.")
        } else if (result.ok) {
          toast.success(`Approved ${result.approved_count} transaction${result.approved_count !== 1 ? "s" : ""}.`)
        } else {
          toast.warning(
            `Approved ${result.approved_count}, failed ${result.failed_count}. Check logs.`,
          )
        }
      },
      onError: (err) => toast.error(`Bulk approve failed: ${err.message}`),
    })
  }

  const allClear = data.total === 0

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Unapproved Inbox</h2>
        {ready_to_approve.length > 0 && (
          <button
            onClick={handleApproveAll}
            disabled={approveAll.isPending}
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium border transition-colors",
              approveAll.isPending
                ? "opacity-50 cursor-not-allowed border-input bg-muted text-muted-foreground"
                : "border-green-300 bg-green-50 text-green-800 hover:bg-green-100 dark:border-green-700 dark:bg-green-950/40 dark:text-green-300 dark:hover:bg-green-900/60",
            )}
          >
            <CheckCheck className="h-4 w-4" />
            {approveAll.isPending
              ? "Approving..."
              : `Approve All Categorized (${ready_to_approve.length})`}
          </button>
        )}
      </div>

      {allClear ? (
        <Card>
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm">No unapproved transactions. Inbox is clear.</p>
          </CardContent>
        </Card>
      ) : (
        <>
          {ready_to_approve.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">
                  Ready to Approve
                  <span className="ml-2 font-normal text-muted-foreground">
                    ({ready_to_approve.length})
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-0">
                <TransactionTable
                  items={ready_to_approve}
                  approvingId={approvingId}
                  onApprove={handleApproveOne}
                  showApproveButton
                />
              </CardContent>
            </Card>
          )}

          {needs_category.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">
                  Needs Category
                  <span className="ml-2 font-normal text-muted-foreground">
                    ({needs_category.length})
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-0">
                <p className="px-6 pb-3 text-xs text-muted-foreground">
                  These transactions are missing a category. Assign a category in YNAB, then sync.
                </p>
                <TransactionTable
                  items={needs_category}
                  approvingId={null}
                  onApprove={() => undefined}
                  showApproveButton={false}
                />
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
