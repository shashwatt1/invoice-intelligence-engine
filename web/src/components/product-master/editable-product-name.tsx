import { Pencil } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { CommercialCandidateRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useEditProductName } from "@/hooks/use-api";
import { ProductName } from "@/pages/product-master-review";

type Props = {
  row: Pick<CommercialCandidateRow, "product_id" | "product_name" | "product_name_basis" | "canonical_identifier">
    & Partial<CommercialCandidateRow>;
  /** How the name is shown when not editing (default: the list's compact name). */
  children?: ReactNode;
  /** False hides the pencil (a role that may not rename). The server enforces this independently. */
  editable?: boolean;
};

/**
 * The product's name with an "Edit name" control, used in the PM Approvals list and in the review pop-up so a
 * name and the units per case can be fixed in one place. Saving makes the typed name the product's canonical name
 * everywhere it is shown; the previous name is kept as a historical alias by the server. Not the PDI export.
 */
export function EditableProductName({ row, children, editable = true }: Props) {
  const edit = useEditProductName();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const start = () => {
    setDraft(row.product_name_basis === "AMBIGUOUS_SOURCE" || !row.product_name ? "" : row.product_name);
    setEditing(true);
  };
  const save = () => {
    const name = draft.trim();
    if (!name || edit.isPending) return;
    edit.mutate({ productId: row.product_id, name }, {
      onSuccess: (result) => {
        toast.success(result.changed ? `Name saved: ${result.name}` : "That name was already set.");
        setEditing(false);
      },
      onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not save the name. Try again."),
    });
  };

  if (editing) {
    return (
      <form className="flex flex-wrap items-center gap-1.5" onSubmit={(event) => { event.preventDefault(); save(); }}>
        <Input value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={100} autoFocus
               aria-label="New product name" disabled={edit.isPending} className="h-8 min-w-0 flex-1"
               onKeyDown={(event) => {
                 if (event.key === "Escape") {
                   // Escape closes the editor only — never the review pop-up around it.
                   event.stopPropagation();
                   setEditing(false);
                 }
               }} />
        <Button type="submit" size="sm" disabled={!draft.trim() || edit.isPending}>Save</Button>
        <Button type="button" variant="ghost" size="sm" disabled={edit.isPending} onClick={() => setEditing(false)}>Cancel</Button>
      </form>
    );
  }
  return (
    <div className="flex items-start gap-1">
      <div className="min-w-0 flex-1">{children ?? <ProductName row={row as CommercialCandidateRow} />}</div>
      {editable ? (
        <Button type="button" variant="ghost" size="icon-xs" className="shrink-0 text-muted-foreground"
                aria-label={`Edit name of ${row.canonical_identifier ?? "this product"}`} title="Edit name" onClick={start}>
          <Pencil aria-hidden />
        </Button>
      ) : null}
    </div>
  );
}
