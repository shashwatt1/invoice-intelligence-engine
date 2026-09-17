import { ArrowDown, ArrowUp, FileText, ImagePlus, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
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

function sameFile(a: File, b: File): boolean {
  return a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
}

function Thumbnail({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file.type.startsWith("image/")) return;
    const next = URL.createObjectURL(file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return (
    <div className="flex size-12 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-accent">
      {url ? (
        <img src={url} alt="" className="size-full object-cover" />
      ) : (
        <FileText className="size-5 text-accent-foreground" />
      )}
    </div>
  );
}

/**
 * The files of ONE invoice, in top-to-bottom order.
 *
 * A long invoice is photographed in overlapping pieces; the operator adds
 * every photo here, in order, and the backend reads them as one intake —
 * one OCR context, one extraction, one invoice. Nothing has to be cropped
 * or trimmed to avoid overlap. A single PDF or photo is simply a list of
 * one. Order matters (it is the photo number the extraction reports), so
 * rows can be moved; a photo can be removed until processing starts.
 */
export function UploadDropzone({
  files,
  onFilesChange,
  disabled,
}: {
  files: File[];
  onFilesChange: (files: File[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [rejection, setRejection] = useState<string | null>(null);

  const accept = useCallback(
    (candidates: File[]) => {
      const problems: string[] = [];
      const accepted: File[] = [];
      for (const candidate of candidates) {
        if (!ACCEPTED_TYPES.includes(candidate.type)) {
          problems.push(`"${candidate.name}" is not a PDF, PNG, or JPEG.`);
        } else if (candidate.size > MAX_SIZE_MB * 1024 * 1024) {
          problems.push(`"${candidate.name}" exceeds the ${MAX_SIZE_MB} MB limit.`);
        } else if (files.some((existing) => sameFile(existing, candidate))) {
          problems.push(`"${candidate.name}" is already in the list.`);
        } else {
          accepted.push(candidate);
        }
      }
      setRejection(problems.length ? problems.join(" ") : null);
      if (accepted.length) onFilesChange([...files, ...accepted]);
    },
    [files, onFilesChange],
  );

  const handleDrop = (event: DragEvent) => {
    event.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    accept(Array.from(event.dataTransfer.files ?? []));
  };

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= files.length) return;
    const next = [...files];
    [next[index], next[target]] = [next[target], next[index]];
    onFilesChange(next);
  };

  const remove = (index: number) => onFilesChange(files.filter((_, i) => i !== index));

  const dropzoneClass = cn(
    "flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed bg-card text-center transition-all duration-200",
    isDragging
      ? "border-primary bg-accent/60 ring-4 ring-primary/10"
      : "border-input hover:border-primary/50 hover:bg-surface-2",
    disabled && "pointer-events-none opacity-60",
  );

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT_ATTR}
        multiple
        className="hidden"
        data-testid="file-input"
        onChange={(event) => {
          accept(Array.from(event.target.files ?? []));
          event.target.value = "";
        }}
      />

      {files.length > 0 ? (
        <div className="space-y-2" data-testid="photo-list">
          <div className="flex items-center justify-between">
            <span className="text-[0.78rem] font-medium">
              Invoice {files.length === 1 ? "file" : `photos · ${files.length}`}
            </span>
            {files.length > 1 ? (
              <span className="text-[0.7rem] text-muted-foreground">
                Top to bottom, in this order — overlap between photos is fine
              </span>
            ) : null}
          </div>
          <ol className="space-y-1.5">
            {files.map((file, index) => (
              <li
                key={`${file.name}-${file.size}-${file.lastModified}`}
                className="surface flex items-center gap-3 p-2.5"
                data-testid="photo-row"
              >
                <span className="w-5 text-center font-mono text-[0.78rem] font-semibold text-muted-foreground">
                  {index + 1}
                </span>
                <Thumbnail file={file} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[0.85rem] font-semibold">{file.name}</div>
                  <div className="text-[0.75rem] text-muted-foreground">
                    {formatSize(file.size)} · {file.type.replace("application/", "").replace("image/", "").toUpperCase()}
                  </div>
                </div>
                {!disabled ? (
                  <div className="flex items-center gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${file.name} up`}
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                    >
                      <ArrowUp className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${file.name} down`}
                      disabled={index === files.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      <ArrowDown className="size-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Remove ${file.name}`}
                      onClick={() => remove(index)}
                    >
                      <X className="size-4" />
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
          {!disabled ? (
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              onDragOver={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={cn(dropzoneClass, "px-4 py-3")}
              data-testid="add-photo"
            >
              <span className="inline-flex items-center gap-2 text-[0.82rem] font-medium">
                <ImagePlus className="size-4" /> Add {files.length === 1 ? "another photo of this invoice" : "photo"}
              </span>
            </button>
          ) : null}
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
          className={cn(dropzoneClass, "px-6 py-16")}
          data-testid="dropzone"
        >
          <div
            className={cn(
              "flex size-11 items-center justify-center rounded-md bg-surface-2 ring-1 ring-foreground/8 transition-transform",
              isDragging && "scale-110",
            )}
          >
            <UploadCloud className="size-6 text-accent-foreground" />
          </div>
          <div>
            <div className="text-[0.95rem] font-semibold tracking-tight">
              {isDragging ? "Drop to upload" : "Upload an invoice"}
            </div>
            <div className="mt-0.5 text-[0.78rem] text-muted-foreground">
              Drag documents here or <span className="font-medium text-primary">browse</span> · one file, or several overlapping photos of one invoice
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            {["PDF", "PNG", "JPEG"].map((format) => (
              <Badge key={format} variant="outline" className="text-[0.66rem]">
                {format}
              </Badge>
            ))}
            <span className="t-meta ml-1">up to {MAX_SIZE_MB} MB each</span>
          </div>
        </button>
      )}

      {rejection ? <p className="text-danger mt-2 text-[0.78rem]">{rejection}</p> : null}
    </div>
  );
}
