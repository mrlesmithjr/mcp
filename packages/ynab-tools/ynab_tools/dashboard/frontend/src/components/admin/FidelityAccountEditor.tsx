import { useState, useEffect, useRef } from "react"
import { X } from "lucide-react"

function parseFidelityAccounts(raw: string): { num: string; name: string }[] {
  if (!raw.trim()) return []
  try {
    const obj = JSON.parse(raw) as Record<string, string>
    return Object.entries(obj).map(([num, name]) => ({ num, name }))
  } catch {
    return []
  }
}

function serializeFidelityAccounts(rows: { num: string; name: string }[]): string {
  const obj: Record<string, string> = {}
  for (const r of rows) {
    if (r.num.trim()) obj[r.num.trim()] = r.name.trim()
  }
  return Object.keys(obj).length ? JSON.stringify(obj) : ""
}

export function FidelityAccountEditor({
  value,
  onChange,
  accounts,
}: {
  value: string
  onChange: (v: string) => void
  accounts: string[]
}) {
  const [rows, setRows] = useState(() => parseFidelityAccounts(value))
  const lastEmitted = useRef(value)

  useEffect(() => {
    if (value !== lastEmitted.current) {
      lastEmitted.current = value
      setRows(parseFidelityAccounts(value))
    }
  }, [value])

  function update(newRows: { num: string; name: string }[]) {
    const serialized = serializeFidelityAccounts(newRows)
    lastEmitted.current = serialized
    setRows(newRows)
    onChange(serialized)
  }

  function setCell(i: number, field: "num" | "name", v: string) {
    update(rows.map((r, idx) => (idx === i ? { ...r, [field]: v } : r)))
  }

  return (
    <div className="space-y-2">
      {rows.length > 0 && (
        <div className="flex gap-2 text-xs text-muted-foreground px-0.5">
          <span className="w-36 shrink-0">Fidelity account #</span>
          <span className="flex-1">YNAB account name</span>
        </div>
      )}
      {rows.map((row, i) => (
        <div key={i} className="flex gap-2 items-center">
          <input
            type="text"
            value={row.num}
            onChange={(e) => setCell(i, "num", e.target.value)}
            placeholder="Account number"
            spellCheck={false}
            className="w-36 shrink-0 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <input
            list={`fidelity-accounts-${i}`}
            type="text"
            value={row.name}
            onChange={(e) => setCell(i, "name", e.target.value)}
            placeholder="YNAB account name"
            className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <datalist id={`fidelity-accounts-${i}`}>
            {accounts.map((a) => <option key={a} value={a} />)}
          </datalist>
          <button
            type="button"
            onClick={() => update(rows.filter((_, idx) => idx !== i))}
            className="text-muted-foreground hover:text-destructive shrink-0"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => update([...rows, { num: "", name: "" }])}
        className="text-xs text-primary hover:underline underline-offset-2"
      >
        + Add account
      </button>
    </div>
  )
}
