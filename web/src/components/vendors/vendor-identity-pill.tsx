import type { VendorIdentityStatus } from "@/api/types";
import { StatusPill } from "@/components/shared/status-badge";

/** A vendor's identity status: unresolved until a manager confirms it. */
export function VendorIdentityPill({ status }: { status: VendorIdentityStatus }) {
  return status === "confirmed" ? (
    <StatusPill size="xs" tone="success" label="Identity confirmed"
                meaning="A manager confirmed this vendor's canonical identity." />
  ) : (
    <StatusPill size="xs" tone="warning" label="Unresolved"
                meaning="Observed on invoices; no one has confirmed which vendor this is yet." />
  );
}
