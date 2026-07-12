import { FileQuestion } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-secondary">
        <FileQuestion className="size-6 text-muted-foreground" />
      </div>
      <div className="text-3xl font-bold tracking-tight">404</div>
      <p className="max-w-sm text-[0.85rem] text-muted-foreground">
        This page doesn't exist. The invoice you're looking for may have been processed under a
        different route.
      </p>
      <Button asChild className="mt-2">
        <Link to="/dashboard">Back to dashboard</Link>
      </Button>
    </div>
  );
}
