import { ArrowDown, ArrowUp, FileText, ImagePlus, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const ACCEPTED_TYPES = ["application/pdf", "image/png", "image/jpeg"];
const ACCEPT_ATTR = ".pdf,.png,.jpg,.jpeg";
const MAX_SIZE_MB = 25;

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

const TYPE_BY_EXTENSION: Record<string, string> = {
  pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
};

/**
 * The file with a usable MIME type. Files dragged from some apps (Photos,
 * some chat and download managers) arrive with an empty or non-standard
 * type, which made them look unsupported and the server would refuse them
 * too (it validates the declared type). The type is then taken from the
 * extension, the same rule the file picker's accept list already uses.
 */
function withKnownType(file: File): File {
  if (ACCEPTED_TYPES.includes(file.type)) return file;
  const fromName = TYPE_BY_EXTENSION[file.name.split(".").pop()?.toLowerCase() ?? ""];
  const type = file.type === "image/jpg" || file.type === "image/pjpeg" ? "image/jpeg" : fromName;
  if (!type || (file.type && !file.type.startsWith("image/") && file.type !== "application/octet-stream")) return file;
  return new File([file], file.name, { type, lastModified: file.lastModified });
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
      for (const original of candidates) {
        const candidate = withKnownType(original);
        if (!ACCEPTED_TYPES.includes(candidate.type)) {
          problems.push(`"${candidate.name}" is not a PDF, PNG, or JPEG.`);
        } else if (candidate.size > MAX_SIZE_MB * 1024 * 1024) {
          problems.push(`"${candidate.name}" exceeds the ${MAX_SIZE_MB} MB limit.`);
        } else if (files.some((existing) => sameFile(existing, candidate))) {
          problems.push(`"${candidate.name}" is already in the list.`);
        } else if (candidate.type === "application/pdf" && (files.length > 0 || accepted.length > 0)) {
          // Several files are photos of ONE invoice; a PDF is processed on its own.
          problems.push(`"${candidate.name}" is a PDF — process a PDF on its own, not together with other files.`);
        } else if ([...files, ...accepted].some((existing) => existing.type === "application/pdf")) {
          problems.push(`"${candidate.name}" was not added: the list already holds a PDF, which is processed on its own.`);
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

  const handleDragOver = (event: DragEvent) => {
    event.preventDefault();
    if (!disabled) setIsDragging(true);
  };

  // Leaving a child (a row, the add button) is not leaving the drop area.
  const handleDragLeave = (event: DragEvent) => {
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
    setIsDragging(false);
  };

  // A file dropped beside the drop area must not make the browser open it and
  // leave the page (losing the list). Only file drags are touched.
  useEffect(() => {
    const swallow = (event: globalThis.DragEvent) => {
      if (Array.from(event.dataTransfer?.types ?? []).includes("Files")) event.preventDefault();
    };
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);
    return () => {
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", swallow);
    };
  }, []);

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= files.length) return;
    const next = [...files];
    [next[index], next[target]] = [next[target], next[index]];
    onFilesChange(next);
  };

  const remove = (index: number) => onFilesChange(files.filter((_, i) => i !== index));

  const dropzoneClass = cn(
    "flex w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed bg-card text-center transition-[border-color,background-color] duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
    isDragging
      ? "border-brand bg-accent/60"
      : "border-input/90 hover:border-brand/60",
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
        <div
          className={cn("space-y-2 rounded-lg transition-[background-color,box-shadow] duration-200",
                        isDragging && !disabled && "bg-accent/40 ring-2 ring-brand/60")}
          data-testid="photo-list"
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
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
              className={cn(dropzoneClass, "px-4 py-3")}
              data-testid="add-photo"
            >
              <span className="inline-flex items-center gap-2 text-[0.82rem] font-medium">
                <ImagePlus className="size-4" />
                {isDragging
                  ? "Drop to add to this invoice"
                  : `Add ${files.length === 1 ? "another photo of this invoice" : "photo"} — or drop more photos anywhere here`}
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
          className={cn(dropzoneClass, "px-6 py-14")}
          data-testid="dropzone"
        >
          <div
            className={cn(
              "flex size-12 items-center justify-center rounded-full bg-accent/70 text-brand transition-transform motion-safe:duration-200",
              isDragging && "scale-110",
            )}
          >
            <UploadCloud className="size-5" strokeWidth={1.8} />
          </div>
          <div>
            <div className="text-[1.02rem] font-semibold tracking-[-0.01em]">
              {isDragging ? "Drop to add" : "Drop the invoice here"}
            </div>
            <div className="mt-1 text-[0.8rem] text-muted-foreground">
              or <span className="font-medium text-brand underline-offset-2 hover:underline">browse your files</span> — a PDF, a photo, or every overlapping photo of one long invoice
            </div>
          </div>
          <div className="t-meta">PDF · PNG · JPEG · up to {MAX_SIZE_MB} MB each</div>
        </button>
      )}

      {rejection ? <p className="text-danger mt-2 text-[0.78rem]">{rejection}</p> : null}
    </div>
  );
}
