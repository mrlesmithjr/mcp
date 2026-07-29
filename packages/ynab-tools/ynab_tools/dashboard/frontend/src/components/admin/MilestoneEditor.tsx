import { useState, useEffect, useRef } from "react"
import { X } from "lucide-react"

function parseMilestones(raw: string): { age: string; label: string }[] {
  if (!raw.trim()) return []
  return raw
    .split(",")
    .map((s) => {
      const idx = s.indexOf(":")
      if (idx === -1) return { age: s.trim(), label: "" }
      return { age: s.slice(0, idx).trim(), label: s.slice(idx + 1).trim() }
    })
    .filter((m) => m.age)
}

function serializeMilestones(rows: { age: string; label: string }[]): string {
  return rows
    .filter((r) => r.age.trim())
    .map((r) => `${r.age.trim()}:${r.label.trim()}`)
    .join(",")
}

const STANDARD_MILESTONES: { age: string; label: string; note: string }[] = [
  { age: "50",  label: "Catch-up contributions",   note: "Extra $7,500/yr to 401k and extra $1,000/yr to IRA become available" },
  { age: "55",  label: "Rule of 55",               note: "Penalty-free 401k withdrawals if you leave your employer at age 55 or later" },
  { age: "59",  label: "Penalty-free withdrawals", note: "All retirement account withdrawals free of 10% early withdrawal penalty" },
  { age: "62",  label: "Early Social Security",    note: "Earliest Social Security eligibility (permanently reduced benefit)" },
  { age: "65",  label: "Medicare eligible",        note: "Medicare Part A and Part B eligibility begins" },
  { age: "67",  label: "Full retirement age",      note: "Full Social Security benefit for those born 1960 or later" },
  { age: "70",  label: "Max Social Security",      note: "Delayed retirement credits stop; maximum possible monthly benefit" },
  { age: "73",  label: "RMDs begin",               note: "Required Minimum Distributions from tax-deferred accounts (SECURE 2.0)" },
]

export function MilestoneEditor({ value, onChange, birthYear }: { value: string; onChange: (v: string) => void; birthYear?: number }) {
  const [rows, setRows] = useState(() => parseMilestones(value))
  const lastEmitted = useRef(value)

  useEffect(() => {
    if (value !== lastEmitted.current) {
      lastEmitted.current = value
      setRows(parseMilestones(value))
    }
  }, [value])

  function update(newRows: { age: string; label: string }[]) {
    const serialized = serializeMilestones(newRows)
    lastEmitted.current = serialized
    setRows(newRows)
    onChange(serialized)
  }

  function setCell(i: number, field: "age" | "label", v: string) {
    update(rows.map((r, idx) => (idx === i ? { ...r, [field]: v } : r)))
  }

  const usedAges = new Set(rows.map((r) => r.age.trim()))
  const currentAge = birthYear ? new Date().getFullYear() - birthYear : 0
  const suggestions = STANDARD_MILESTONES.filter(
    (s) => !usedAges.has(s.age) && parseInt(s.age) > currentAge
  )

  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex gap-2 items-center">
          <input
            type="number"
            value={row.age}
            onChange={(e) => setCell(i, "age", e.target.value)}
            placeholder="Age"
            min={1}
            max={120}
            className="w-20 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <input
            type="text"
            value={row.label}
            onChange={(e) => setCell(i, "label", e.target.value)}
            placeholder="Label"
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
        onClick={() => update([...rows, { age: "", label: "" }])}
        className="text-xs text-primary hover:underline underline-offset-2"
      >
        + Add custom milestone
      </button>
      {suggestions.length > 0 && (
        <div className="pt-1 space-y-1">
          <p className="text-xs text-muted-foreground">Suggested:</p>
          <div className="flex flex-wrap gap-1.5">
            {suggestions.map((s) => (
              <button
                key={s.age}
                type="button"
                title={s.note}
                onClick={() => update([...rows, { age: s.age, label: s.label }])}
                className="inline-flex items-center gap-1 rounded-full border border-input bg-background px-2.5 py-0.5 text-xs hover:bg-muted"
              >
                <span className="font-medium text-muted-foreground">Age {s.age}</span>
                <span>{s.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
