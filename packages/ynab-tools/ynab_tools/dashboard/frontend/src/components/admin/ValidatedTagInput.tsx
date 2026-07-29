import { useState, useEffect, useRef } from "react"
import { AlertTriangle, X } from "lucide-react"

function isTagKnown(tag: string, suggestions: string[], matchType: "exact" | "fragment" = "exact"): boolean {
  if (suggestions.length === 0) return true
  const v = tag.trim().toLowerCase()
  if (matchType === "fragment") {
    return v.length > 0 && suggestions.some((s) => s.toLowerCase().includes(v))
  }
  return suggestions.some((s) => s.toLowerCase() === v)
}

export function ValidatedTagInput({
  value,
  onChange,
  suggestions,
  placeholder,
  matchType = "exact",
}: {
  value: string
  onChange: (v: string) => void
  suggestions: string[]
  placeholder?: string
  matchType?: "exact" | "fragment"
}) {
  const tags = value ? value.split(",").map((t) => t.trim()).filter(Boolean) : []
  const [input, setInput] = useState("")
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [highlightIdx, setHighlightIdx] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered = input.trim()
    ? suggestions.filter((s) => s.toLowerCase().includes(input.toLowerCase()) && !tags.includes(s)).slice(0, 10)
    : suggestions.filter((s) => !tags.includes(s)).slice(0, 15)

  useEffect(() => {
    setHighlightIdx(-1)
  }, [input])

  function addTag(raw: string) {
    const t = raw.trim()
    if (t && !tags.includes(t)) {
      onChange([...tags, t].join(","))
    }
    setInput("")
    setDropdownOpen(false)
  }

  function removeTag(idx: number) {
    onChange(tags.filter((_, i) => i !== idx).join(","))
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setHighlightIdx((i) => Math.min(i + 1, filtered.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setHighlightIdx((i) => Math.max(i - 1, -1))
    } else if (e.key === "Escape") {
      setDropdownOpen(false)
    } else if (e.key === "Enter" || e.key === ",") {
      e.preventDefault()
      if (highlightIdx >= 0 && filtered[highlightIdx]) {
        addTag(filtered[highlightIdx])
      } else {
        addTag(input)
      }
    } else if (e.key === "Backspace" && !input && tags.length > 0) {
      removeTag(tags.length - 1)
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <div
        className="min-h-9 flex flex-wrap gap-1 items-center rounded-md border border-input bg-background px-2 py-1.5 text-sm focus-within:ring-1 focus-within:ring-ring cursor-text"
        onClick={() => inputRef.current?.focus()}
      >
        {tags.map((tag, i) => {
          const known = isTagKnown(tag, suggestions, matchType)
          return (
            <span
              key={i}
              title={known ? undefined : "Not found in current YNAB data"}
              className={`flex items-center gap-1 rounded pl-2 pr-1 py-0.5 text-xs font-medium ${
                known
                  ? "bg-muted text-foreground"
                  : "bg-amber-100 text-amber-800 border border-amber-300 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-700"
              }`}
            >
              {!known && <AlertTriangle className="h-2.5 w-2.5 shrink-0" />}
              {tag}
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); removeTag(i) }}
                className="text-current opacity-60 hover:opacity-100 rounded"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          )
        })}
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value)
            setDropdownOpen(true)
          }}
          onKeyDown={handleKeyDown}
          onFocus={() => setDropdownOpen(true)}
          onBlur={() => {
            setTimeout(() => {
              if (input.trim()) addTag(input)
              setDropdownOpen(false)
            }, 150)
          }}
          placeholder={tags.length === 0 ? placeholder : "Add..."}
          className="flex-1 min-w-28 bg-transparent outline-none text-sm placeholder:text-muted-foreground"
        />
      </div>
      {dropdownOpen && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-input bg-background shadow-md max-h-48 overflow-y-auto">
          {filtered.map((s, i) => (
            <button
              key={s}
              type="button"
              onMouseDown={(e) => { e.preventDefault(); addTag(s) }}
              className={`w-full text-left px-3 py-1.5 text-sm hover:bg-muted ${
                i === highlightIdx ? "bg-muted" : ""
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
