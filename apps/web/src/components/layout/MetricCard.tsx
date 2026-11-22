import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export const MetricCard = ({ title, value, tone }: { title: string; value: number; tone?: "crit" }) => (
  <Card className="p-5">
    <p className="text-fg2">{title}</p>
    <p className={cn("mt-3 font-mono text-[32px] font-medium leading-none tabular-nums", tone === "crit" && value > 0 && "text-crit")}>
      {value.toLocaleString("en-US")}
    </p>
  </Card>
);
