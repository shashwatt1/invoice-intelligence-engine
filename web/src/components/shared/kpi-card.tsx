import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function KpiCard({
  label,
  value,
  hint,
  icon: Icon,
  index = 0,
  valueClassName,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
  index?: number;
  valueClassName?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, delay: index * 0.05, ease: "easeOut" }}
    >
      <Card className="gap-1.5 px-4 py-3.5">
        <div className="flex items-center justify-between">
          <span className="text-[0.68rem] font-semibold tracking-wider text-muted-foreground uppercase">
            {label}
          </span>
          {Icon ? <Icon className="size-3.5 text-muted-foreground/70" /> : null}
        </div>
        <div className={cn("text-[1.45rem] leading-none font-bold tabular-nums", valueClassName)}>
          {value}
        </div>
        {hint ? <div className="text-[0.72rem] text-muted-foreground">{hint}</div> : null}
      </Card>
    </motion.div>
  );
}

export function KpiCardSkeleton() {
  return (
    <Card className="gap-2 px-4 py-3.5">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-7 w-14" />
      <Skeleton className="h-3 w-24" />
    </Card>
  );
}
