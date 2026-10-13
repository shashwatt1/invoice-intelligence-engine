import { ExternalLink, Link2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { ReferenceLink } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/use-auth";
import { useSaveReferenceLink } from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";
import { referenceUrlProblem, safeHref } from "@/lib/reference-link";

/**
 * Original invoice / chat link: reopen the source image or its chat message while
 * mapping or generating EDI. Shown to everyone who can see the invoice; saved,
 * changed or removed by a manager or admin. Opened in a new tab; never fetched
 * or previewed here, and a private chat link still needs the viewer's own access.
 */
export function ReferenceLinkCard({ invoiceId, link }: { invoiceId: string; link: ReferenceLink | null | undefined }) {
  const { hasRole } = useAuth();
  const canEdit = hasRole("MANAGER");
  const save = useSaveReferenceLink(invoiceId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const href = safeHref(link?.url);
  const problem = referenceUrlProblem(draft);

  const submit = (value: string | null) => {
    save.mutate(value, {
      onSuccess: () => {
        setEditing(false);
        toast.success(value ? "Original invoice link saved." : "Original invoice link removed.");
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : "The link was not saved."),
    });
  };

  return (
    <Card className="gap-2 p-0" data-testid="reference-link-card">
      <CardHeader className="px-4 pt-3 pb-0">
        <CardTitle className="flex items-center gap-1.5 text-[0.85rem]">
          <Link2 className="size-3.5" /> Original invoice / Chat link
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 px-4 pb-3 text-[0.8rem]">
        {href && !editing ? (
          <div className="space-y-1">
            <a href={href} target="_blank" rel="noopener noreferrer"
               className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
               data-testid="reference-link-open">
              Open original invoice <ExternalLink className="size-3" />
            </a>
            <div className="t-meta">
              Saved by {link?.set_by ?? "unknown"}{link?.set_at ? ` · ${formatDateTime(link.set_at)}` : ""}. Opens in a new
              tab; a chat link still needs your own access to that chat.
            </div>
          </div>
        ) : !editing ? (
          <div className="t-meta">No link saved for this invoice.</div>
        ) : null}

        {editing ? (
          <div className="space-y-1.5">
            <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="https://…" autoFocus
                   aria-label="Original invoice or chat link" disabled={save.isPending} />
            {problem ? <div className="text-danger text-[0.72rem]" role="alert">{problem}</div> : null}
            <div className="flex gap-2">
              <Button size="sm" onClick={() => submit(draft.trim() || null)}
                      disabled={save.isPending || problem !== null || !draft.trim()}>
                {save.isPending ? "Saving…" : "Save link"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={save.isPending}>Cancel</Button>
            </div>
          </div>
        ) : canEdit ? (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => { setDraft(link?.url ?? ""); setEditing(true); }}>
              {link ? "Change link" : "Add link"}
            </Button>
            {link ? (
              <Button size="sm" variant="ghost" onClick={() => submit(null)} disabled={save.isPending}>Remove</Button>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
