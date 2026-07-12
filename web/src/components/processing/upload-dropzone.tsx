import { FileText, UploadCloud, X } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { DragEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"];
const ACCEPT_ATTR = ".pdf,.png,.jpg,.jpeg";
const MAX_SIZE_MB = 25;

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function UploadDropzone({
  file,
  onFileSelected,
  onClear,
  disabled,
}: {
  file: File | null;
  onFileSelected: (file: File) => void;
  onClear: () => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [rejection, setRejection] = useState<string | null>(null);

  const accept = useCallback(
    (candidate: File) => {
      if (!ACCEPTED_TYPES.includes(candidate.type)) {
        setRejection(`"${candidate.name}" is not a PDF, PNG, or JPEG.`);
        return;
      }
      if (candidate.size > MAX_SIZE_MB * 1024 * 1024) {
        setRejection(`"${candidate.name}" exceeds the ${MAX_SIZE_MB} MB limit.`);
        return;
      }
      setRejection(null);
      onFileSelected(candidate);
    },
    [onFileSelected],
  );

  const handleDrop = (event: DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) accept(dropped);
  };

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        className="hidden"
        onChange={(event) => {
          const chosen = event.target.files?.[0];
          if (chosen) accept(chosen);
          event.target.value = "";
        }}
      />

      {file ? (
        <div className="flex items-center gap-3 rounded-xl border bg-card p-4">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-accent">
            <FileText className="size-5 text-accent-foreground" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[0.85rem] font-semibold">{file.name}</div>
            <div className="text-[0.75rem] text-muted-foreground">
              {formatSize(file.size)} · {file.type.replace("application/", "").toUpperCase()}
            </div>
          </div>
          {!disabled && (
            <Button variant="ghost" size="icon-sm" onClick={onClear} aria-label="Remove file">
              <X className="size-4" />
            </Button>
          )}
        </div>
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            if (!disabled) setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          className={cn(
            "flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed bg-card px-6 py-14 text-center transition-all",
            isDragging
              ? "border-primary bg-accent/60 scale-[1.01]"
              : "border-border hover:border-primary/50 hover:bg-secondary/40",
            disabled && "pointer-events-none opacity-60",
          )}
        >
          <div
            className={cn(
              "flex size-12 items-center justify-center rounded-full bg-accent transition-transform",
              isDragging && "scale-110",
            )}
          >
            <UploadCloud className="size-6 text-accent-foreground" />
          </div>
          <div>
            <div className="text-[0.9rem] font-semibold">
              {isDragging ? "Drop to upload" : "Drag & drop an invoice"}
            </div>
            <div className="mt-0.5 text-[0.78rem] text-muted-foreground">
              or <span className="font-medium text-primary">browse files</span> · up to{" "}
              {MAX_SIZE_MB} MB
            </div>
          </div>
          <div className="flex gap-1.5">
            {["PDF", "PNG", "JPEG"].map((format) => (
              <Badge key={format} variant="secondary" className="text-[0.68rem]">
                {format}
              </Badge>
            ))}
          </div>
        </button>
      )}

      {rejection ? <p className="text-danger mt-2 text-[0.78rem]">{rejection}</p> : null}
    </div>
  );
}
