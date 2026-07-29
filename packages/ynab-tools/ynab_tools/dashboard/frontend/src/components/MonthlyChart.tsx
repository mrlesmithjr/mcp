import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import type { ChartConfig } from "@/components/ui/chart"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
} from "@/components/ui/chart"
import { formatMonth } from "@/lib/api"
import type { MonthSummary } from "@/lib/api"

const chartConfig = {
  income: { label: "Income", color: "oklch(0.627 0.194 149.214)" },
  spending: { label: "Spending", color: "oklch(0.637 0.237 25.331)" },
} satisfies ChartConfig

interface MonthlyChartProps {
  data: MonthSummary[]
}

export function MonthlyChart({ data }: MonthlyChartProps) {
  const chartData = data.map((m) => ({
    month: formatMonth(m.month),
    income: m.income,
    spending: m.spending,
  }))

  return (
    <ChartContainer config={chartConfig} className="min-h-[220px] w-full">
      <BarChart data={chartData} barGap={4}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 12 }}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 12 }}
          tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
          width={48}
        />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="income" fill="var(--color-income)" radius={[4, 4, 0, 0]} />
        <Bar dataKey="spending" fill="var(--color-spending)" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ChartContainer>
  )
}
