import { useState, useEffect, useRef } from "react"
import { X } from "lucide-react"

function parsePairs(raw: string): { col1: string; col2: string }[] {
  if (!raw.trim()) return []
  return raw
    .split(",")
    .map((entry) => {
      const idx = entry.indexOf("|")
      if (idx === -1) return { col1: entry.trim(), col2: "" }
      return { col1: entry.slice(0, idx).trim(), col2: entry.slice(idx + 1).trim() }
    })
    .filter((r) => r.col1 || r.col2)
}

function serializePairs(rows: { col1: string; col2: string }[]): string {
  return rows
    .filter((r) => r.col1.trim())
    .map((r) => `${r.col1.trim()}|${r.col2.trim()}`)
    .join(",")
}

export function PairEditor({
  value,
  onChange,
  col1Label,
  col2Label,
  col1Suggestions,
}: {
  value: string
  onChange: (v: string) => void
  col1Label: string
  col2Label: string
  col1Suggestions?: string[]
}) {
  const [rows, setRows] = useState(() => parsePairs(value))
  const lastEmitted = useRef(value)

  useEffect(() => {
    if (value !== lastEmitted.current) {
      lastEmitted.current = value
      setRows(parsePairs(value))
    }
  }, [value])

  function update(newRows: { col1: string; col2: string }[]) {
    const serialized = serializePairs(newRows)
    lastEmitted.current = serialized
    setRows(newRows)
    onChange(serialized)
  }

  function setCell(i: number, field: "col1" | "col2", v: string) {
    update(rows.map((r, idx) => (idx === i ? { ...r, [field]: v } : r)))
  }

  return (
    <div className="space-y-2">
      {rows.length > 0 && (
        <div className="flex gap-2 text-xs text-muted-foreground px-0.5">
          <span className="w-44 shrink-0">{col1Label}</span>
          <span className="flex-1">{col2Label}</span>
        </div>
      )}
      {rows.map((row, i) => (
        <div key={i} className="flex gap-2 items-center">
          <input
            list={col1Suggestions ? `pair-col1-${i}` : undefined}
            type="text"
            value={row.col1}
            onChange={(e) => setCell(i, "col1", e.target.value)}
            placeholder={col1Label}
            spellCheck={false}
            className="w-44 shrink-0 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-ring"
          />
          {col1Suggestions && (
            <datalist id={`pair-col1-${i}`}>
              {col1Suggestions.map((s) => <option key={s} value={s} />)}
            </datalist>
          )}
          <input
            type="text"
            value={row.col2}
            onChange={(e) => setCell(i, "col2", e.target.value)}
            placeholder={col2Label}
            className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          />
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
        onClick={() => update([...rows, { col1: "", col2: "" }])}
        className="text-xs text-primary hover:underline underline-offset-2"
      >
        + Add entry
      </button>
    </div>
  )
}
