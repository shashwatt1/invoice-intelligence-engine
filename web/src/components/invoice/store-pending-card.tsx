import { MapPinOff } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAssignInvoiceStore, useStores } from "@/hooks/use-api";
import { rememberedReviewer, rememberReviewer } from "@/lib/reviewer";

/**
 * The invoice was read before its store was known. Everything the
 * invoice itself says is here and can be corrected; nothing that belongs
 * to a store — reference data, case mappings, proposals, the EDI — is
 * consulted until a person assigns one. Which store is a decision, not a
 * guess, so it is recorded with a name.
 */
export function StorePendingCard({ invoiceId }: { invoiceId: string }) {
  const stores = useStores();
  const assign = useAssignInvoiceStore(invoiceId);
  const [storeId, setStoreId] = useState("");
  const [assignedBy, setAssignedBy] = useState(rememberedReviewer);
  const [note, setNote] = useState("");
  const name = assignedBy.trim();

  const submit = () => {
    if (!storeId || !name) return;
    assign.mutate(
      { storeId, assignedBy: name, note: note.trim() || null },
      {
        onSuccess: (detail) => {
          rememberReviewer(name);
          toast.success(`Store assigned: ${detail.store?.label ?? "store"}. Reference data and case mappings now apply.`);
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "Could not assign the store."),
      },
    );
  };

  return (
    <Card className="border-warning/50 bg-warning-soft/30" data-testid="store-pending">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-[0.95rem]">
          <MapPinOff className="size-4 text-warning" /> Store not yet assigned
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-[0.8rem] text-muted-foreground">
          This invoice was read before its store was known. Its values can be corrected now. Reference
          data, case mappings, Data Review proposals and the PDI export belong to a store and wait until
          you assign one. Nothing is guessed.
        </p>
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="space-y-1">
            <label className="text-[0.72rem] font-medium">Store <span className="text-danger">*</span></label>
            <Select value={storeId} onValueChange={setStoreId} disabled={assign.isPending}>
              <SelectTrigger className="w-full"><SelectValue placeholder="Choose the store" /></SelectTrigger>
              <SelectContent>
                {(stores.data ?? []).map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.label}
                    {s.address ? <span className="ml-2 text-[0.72rem] text-muted-foreground">{s.address}</span> : null}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <label htmlFor="assign-by" className="text-[0.72rem] font-medium">Assigned by <span className="text-danger">*</span></label>
            <Input id="assign-by" value={assignedBy} onChange={(e) => setAssignedBy(e.target.value)}
                   placeholder="e.g. data-team:shashwat" autoComplete="off" disabled={assign.isPending} />
          </div>
          <div className="space-y-1">
            <label htmlFor="assign-note" className="text-[0.72rem] font-medium">Note <span className="text-muted-foreground">(optional)</span></label>
            <Input id="assign-note" value={note} onChange={(e) => setNote(e.target.value)}
                   placeholder="how you know" maxLength={500} disabled={assign.isPending} />
          </div>
        </div>
        <Button size="sm" disabled={!storeId || !name || assign.isPending} onClick={submit} data-testid="assign-store">
          {assign.isPending ? "Assigning…" : "Assign store"}
        </Button>
        <p className="text-[0.7rem] text-muted-foreground">
          Not in the directory? Add it under Stores first — a store is a location a person names, never a
          number read off an invoice.
        </p>
      </CardContent>
    </Card>
  );
}
