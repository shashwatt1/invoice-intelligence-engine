import { History, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { CorrectionEntry, InvoiceDetail, LineItem, LineItemCreate } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAddLineItem, useCorrectInvoiceDate, useCorrectTotals, useVoidLineItem } from "@/hooks/use-api";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { outcomeToast } from "@/lib/corrections";
import { rememberReviewer } from "@/lib/reviewer";

/**
 * Manual corrections to ONE invoice: who is correcting, a row the photos
 * missed, a row that is not on the invoice, and the printed totals. Each
 * change is attributed, timestamped and kept with its old value; the
 * invoice is judged again by the same rules after each one. None of it
 * is master data.
 */

/** The name recorded on every correction from this page. */
export function CorrectingAs({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-[0.75rem]">
      <span className="font-medium whitespace-nowrap">Correcting as <span className="text-danger">*</span></span>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="your name — recorded on every change"
        className="h-7 w-56 text-[0.78rem]"
        autoComplete="off"
        data-testid="correcting-as"
      />
    </label>
  );
}

export function HistoryNote({ history }: { history: CorrectionEntry[] }) {
  if (!history.length) return null;
  const lines = history.map((h) =>
    `${h.field}: ${h.old === null || h.old === undefined ? "—" : String(h.old)} → ${h.new === null || h.new === undefined ? "—" : String(h.new)} · ${h.by ?? "unattributed"} · ${formatDateTime(h.at)}${h.note ? ` · ${h.note}` : ""}`,
  );
  return (
    <span className="inline-flex items-center text-muted-foreground" title={lines.join("\n")} data-testid="history-note">
      <History className="size-3" />
    </span>
  );
}

export function VoidRowButton({ invoiceId, item, by }: { invoiceId: string; item: LineItem; by: string }) {
  const voidRow = useVoidLineItem(invoiceId);
  if (item.line_type === "voided") return null;
  return (
    <Button
      size="sm"
      variant="ghost"
      className="h-6 px-1 text-muted-foreground"
      disabled={!by.trim() || voidRow.isPending}
      title={!by.trim() ? "Enter your name above first" : "Not on the invoice — void this row (kept for audit)"}
      aria-label={`Void ${item.description}`}
      onClick={() => {
        const note = window.prompt(`Void "${item.description}"? Optional note:`) ?? null;
        if (note === null) return;
        voidRow.mutate(
          { sortOrder: item.sort_order, voidedBy: by.trim(), note: note.trim() || null },
          { onSuccess: outcomeToast, onError: (e) => toast.error(e instanceof Error ? e.message : "Could not void.") },
        );
      }}
    >
      <Trash2 className="size-3" />
    </Button>
  );
}

const EMPTY: LineItemCreate = { description: "", product_code: "", quantity: "1", unit_price: "", unit_deposit: "", unit_discount: "", line_total: "", pack_size: "", added_by: "", note: "" };

export function AddRowForm({ invoiceId, by }: { invoiceId: string; by: string }) {
  const add = useAddLineItem(invoiceId);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<LineItemCreate>(EMPTY);
  const set = (key: keyof LineItemCreate) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((prev) => ({ ...prev, [key]: event.target.value }));
  const num = (v: string | null | undefined) => (v && v.trim() !== "" ? v.trim() : null);
  const ready = form.description.trim() && form.quantity.trim() !== "" && Number(form.quantity) >= 0 && by.trim();

  if (!open) {
    return (
      <Button size="sm" variant="outline" onClick={() => setOpen(true)} data-testid="add-row-toggle">
        <Plus className="size-3.5" /> Add a missing line
      </Button>
    );
  }
  const submit = () => {
    if (!ready) return;
    add.mutate(
      {
        description: form.description.trim(), product_code: num(form.product_code), pack_size: num(form.pack_size),
        quantity: form.quantity.trim(), unit_price: num(form.unit_price), unit_deposit: num(form.unit_deposit),
        unit_discount: num(form.unit_discount), line_total: num(form.line_total),
        added_by: by.trim(), note: num(form.note),
      },
      {
        onSuccess: (result) => {
          rememberReviewer(by.trim());
          setForm(EMPTY);
          setOpen(false);
          outcomeToast(result);
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Could not add the line."),
      },
    );
  };
  return (
    <div className="space-y-2 rounded-lg border bg-card p-3" data-testid="add-row-form">
      <div className="text-[0.78rem] font-medium">Add a line the photos missed — to this invoice</div>
      <div className="grid gap-2 sm:grid-cols-4">
        <Input aria-label="Description" placeholder="Description *" value={form.description} onChange={set("description")} className="sm:col-span-2" />
        <Input aria-label="UPC" placeholder="UPC / item code" value={form.product_code ?? ""} onChange={set("product_code")} className="font-mono" />
        <Input aria-label="Package" placeholder="Package (e.g. C-12 12OZ)" value={form.pack_size ?? ""} onChange={set("pack_size")} />
        <Input aria-label="Quantity" placeholder="Qty *" type="number" min={0} step="1" value={form.quantity} onChange={set("quantity")} />
        <Input aria-label="Unit price" placeholder="Unit price" type="number" min={0} step="0.01" value={form.unit_price ?? ""} onChange={set("unit_price")} />
        <Input aria-label="Deposit" placeholder="Deposit / unit" type="number" min={0} step="0.01" value={form.unit_deposit ?? ""} onChange={set("unit_deposit")} />
        <Input aria-label="Discount" placeholder="Discount / unit" type="number" min={0} step="0.01" value={form.unit_discount ?? ""} onChange={set("unit_discount")} />
        <Input aria-label="Line total" placeholder="Line total (blank = qty × price)" type="number" min={0} step="0.01" value={form.line_total ?? ""} onChange={set("line_total")} className="sm:col-span-2" />
        <Input aria-label="Note" placeholder="Note (e.g. on photo 2, missed)" value={form.note ?? ""} onChange={set("note")} className="sm:col-span-2" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={!ready || add.isPending} onClick={submit} data-testid="add-row-submit">
          {add.isPending ? "Adding…" : "Add line and revalidate"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        {!by.trim() ? <span className="text-[0.7rem] text-danger">Enter your name above first.</span> : null}
        <span className="text-[0.7rem] text-muted-foreground">Invoice data only — a case mapping for a new product still goes through Data Review.</span>
      </div>
    </div>
  );
}

const TOTAL_FIELDS = ["subtotal", "tax_amount", "discount_amount", "deposit_total", "fuel_surcharge", "grand_total"] as const;
type TotalField = (typeof TOTAL_FIELDS)[number];
const LABEL: Record<TotalField, string> = {
  subtotal: "Subtotal", tax_amount: "Tax", discount_amount: "Discount", deposit_total: "Deposit total",
  fuel_surcharge: "Fuel / delivery", grand_total: "Grand total",
};

export function EditableTotal({
  detail, field, by, emphasized, readOnly = false,
}: { detail: InvoiceDetail; field: TotalField; by: string; emphasized?: boolean; readOnly?: boolean }) {
  const correct = useCorrectTotals(detail.invoice_id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const value = detail[field];
  const corrected = detail.corrected_fields.includes(field);
  const history = detail.correction_history.filter((h) => h.field === field);

  const save = () => {
    if (draft.trim() === "" || Number.isNaN(Number(draft)) || Number(draft) < 0 || !by.trim()) return;
    const note = window.prompt(`Correct ${LABEL[field]} to ${draft}? Optional note (what you read on the invoice):`) ?? null;
    if (note === null) return;
    correct.mutate(
      { [field]: draft, corrected_by: by.trim(), note: note.trim() || null },
      {
        onSuccess: (result) => {
          rememberReviewer(by.trim());
          setEditing(false);
          outcomeToast(result);
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Correction failed."),
      },
    );
  };

  if (readOnly) {
    return (
      <div className={`flex items-center justify-between py-1 text-[0.82rem] ${emphasized ? "font-semibold" : ""}`} data-testid={`total-${field}`}>
        <span className="flex items-center gap-1.5 text-muted-foreground">{LABEL[field]} <HistoryNote history={history} /></span>
        <span className={`tabular-nums ${value === null ? "text-warning" : ""}`}>
          {value === null ? "not extracted" : formatMoney(value, field === "grand_total" ? detail.currency : undefined)}
        </span>
      </div>
    );
  }

  return (
    <div className={`flex items-center justify-between py-1 text-[0.82rem] ${emphasized ? "font-semibold" : ""}`} data-testid={`total-${field}`}>
      <span className="flex items-center gap-1.5 text-muted-foreground">{LABEL[field]} <HistoryNote history={history} /></span>
      {editing ? (
        <span className="flex items-center gap-1">
          <Input type="number" min={0} step="0.01" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                 aria-label={`Correct ${LABEL[field]}`} className="h-7 w-28 text-right tabular-nums"
                 onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} />
          <Button size="sm" className="h-7 px-2" disabled={correct.isPending || !by.trim()} onClick={save}>Save</Button>
          <Button size="sm" variant="ghost" className="h-7 px-1" onClick={() => setEditing(false)}>×</Button>
        </span>
      ) : (
        <button
          type="button"
          className={`tabular-nums ${corrected ? "underline decoration-dotted underline-offset-2" : ""} ${value === null ? "text-warning" : ""}`}
          title={corrected ? "Corrected by hand — the extracted value is in the history" : "Click to correct"}
          onClick={() => { setDraft(value === null ? "" : String(value)); setEditing(true); }}
          aria-label={`Edit ${LABEL[field]}`}
        >
          {value === null ? "not extracted" : formatMoney(value, field === "grand_total" ? detail.currency : undefined)}
        </button>
      )}
    </div>
  );
}

/**
 * The invoice date as printed — entered by a person when extraction
 * missed it, or left explicitly unknown when the document does not show
 * it. Never inferred from upload, processing or file dates.
 */
export function EditableInvoiceDate({ detail, by, readOnly = false }: { detail: InvoiceDetail; by: string; readOnly?: boolean }) {
  const correct = useCorrectInvoiceDate(detail.invoice_id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const value = detail.invoice_date;
  const corrected = detail.corrected_fields.includes("invoice_date");
  const history = detail.correction_history.filter((h) => h.field === "invoice_date");

  const submit = (next: string | null) => {
    if (!by.trim()) return;
    const what = next === null ? "Record the invoice date as unknown" : `Correct the invoice date to ${formatDate(next)}`;
    const note = window.prompt(`${what}? Optional note (where on the document you read it):`) ?? null;
    if (note === null) return;
    correct.mutate(
      { invoice_date: next, corrected_by: by.trim(), note: note.trim() || null },
      {
        onSuccess: (result) => {
          rememberReviewer(by.trim());
          setEditing(false);
          outcomeToast(result);
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Correction failed."),
      },
    );
  };

  if (readOnly) {
    return (
      <span className="t-meta inline-flex items-center gap-1.5">
        {value === null ? "date unknown" : `dated ${formatDate(value)}`}
        <HistoryNote history={history} />
      </span>
    );
  }

  if (editing) {
    return (
      <span className="inline-flex items-center gap-1" data-testid="invoice-date-editor">
        <Input type="date" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Invoice date"
               className="h-7 w-36 text-[0.78rem]"
               onKeyDown={(e) => { if (e.key === "Enter" && draft) submit(draft); if (e.key === "Escape") setEditing(false); }} />
        <Button size="sm" className="h-7 px-2" disabled={!draft || correct.isPending || !by.trim()} onClick={() => submit(draft)}>Save</Button>
        {value !== null ? (
          <Button size="sm" variant="ghost" className="h-7 px-2 text-muted-foreground" disabled={correct.isPending || !by.trim()}
                  onClick={() => submit(null)} title="The document does not show a legible date">Mark unknown</Button>
        ) : null}
        <Button size="sm" variant="ghost" className="h-7 px-1" onClick={() => setEditing(false)} aria-label="Cancel">×</Button>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <button
        type="button"
        className={`t-meta inline-flex items-center gap-1 rounded-sm underline-offset-2 hover:underline ${value === null ? "font-medium text-warning" : ""} ${corrected ? "underline decoration-dotted" : ""}`}
        title={!by.trim() ? "Enter your name in \"Correcting as\" to change the date" : corrected ? "Entered by hand — the extracted value is in the history" : "Click to correct the invoice date"}
        onClick={() => { setDraft(value ?? ""); setEditing(true); }}
        aria-label={value === null ? "Enter the invoice date" : "Edit the invoice date"}
        data-testid="invoice-date"
      >
        {value === null ? "date unknown — enter it from the document" : `dated ${formatDate(value)}`}
      </button>
      <HistoryNote history={history} />
    </span>
  );
}
