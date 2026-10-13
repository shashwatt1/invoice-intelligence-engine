import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { UploadDropzone } from "./upload-dropzone";

/**
 * Dropping more photos once one is already listed: the whole list is a drop
 * target (not only the small "add" button), each drop is counted once, a stray
 * drop beside the list never makes the browser open the file, a PDF is
 * processed on its own, and the existing limits still apply.
 */

function img(name: string, at = 1, type = "image/jpeg") {
  return new File([new Uint8Array(2048)], name, { type, lastModified: at });
}

function pdf(name: string, at = 1) {
  return img(name, at, "application/pdf");
}

function Harness({ initial = [] as File[], disabled = false }) {
  const [files, setFiles] = useState<File[]>(initial);
  return (
    <>
      <UploadDropzone files={files} onFilesChange={setFiles} disabled={disabled} />
      <output data-testid="order">{files.map((f) => f.name).join(",")}</output>
    </>
  );
}

const drop = (target: Element, files: File[]) => fireEvent.drop(target, { dataTransfer: { files, types: ["Files"] } });
const order = () => screen.getByTestId("order").textContent;

describe("dropping more photos onto an existing list", () => {
  it("accepts a drop anywhere on the list, in order", () => {
    render(<Harness initial={[img("p1.jpg", 1)]} />);
    drop(screen.getByTestId("photo-list"), [img("p2.jpg", 2), img("p3.jpg", 3)]);
    expect(order()).toBe("p1.jpg,p2.jpg,p3.jpg");
  });

  it("accepts a drop on a listed row (it reaches the list once)", () => {
    render(<Harness initial={[img("p1.jpg", 1)]} />);
    drop(screen.getAllByTestId("photo-row")[0], [img("p2.jpg", 2)]);
    expect(order()).toBe("p1.jpg,p2.jpg");
  });

  it("counts a drop on the add button once, not twice", () => {
    render(<Harness initial={[img("p1.jpg", 1)]} />);
    drop(screen.getByTestId("add-photo"), [img("p2.jpg", 2)]);
    expect(order()).toBe("p1.jpg,p2.jpg");
    expect(screen.queryByText(/already in the list/)).toBeNull();
  });

  it("shows that the list is a drop target while dragging", () => {
    render(<Harness initial={[img("p1.jpg", 1)]} />);
    fireEvent.dragOver(screen.getByTestId("photo-list"), { dataTransfer: { types: ["Files"] } });
    expect(screen.getByTestId("add-photo")).toHaveTextContent("Drop to add to this invoice");
    expect(screen.getByTestId("photo-list").className).toContain("ring-2");
  });

  it("refuses a duplicate, an unsupported type and an oversized file, keeping the rest", () => {
    render(<Harness initial={[img("p1.jpg", 1)]} />);
    const big = img("huge.jpg", 9);
    Object.defineProperty(big, "size", { value: 26 * 1024 * 1024 });
    const txt = new File(["x"], "notes.txt", { type: "text/plain" });
    drop(screen.getByTestId("photo-list"), [img("p1.jpg", 1), txt, big, img("p2.jpg", 2)]);
    expect(order()).toBe("p1.jpg,p2.jpg");
    expect(screen.getByText(/"p1.jpg" is already in the list/)).toBeInTheDocument();
    expect(screen.getByText(/notes\.txt.*not a PDF, PNG, or JPEG/)).toBeInTheDocument();
    expect(screen.getByText(/huge\.jpg.*exceeds the 25 MB limit/)).toBeInTheDocument();
  });

  it("keeps a PDF on its own: no PDF added to photos, no photos added to a PDF", () => {
    const { unmount } = render(<Harness initial={[img("p1.jpg", 1)]} />);
    drop(screen.getByTestId("photo-list"), [pdf("inv.pdf", 2)]);
    expect(order()).toBe("p1.jpg");
    expect(screen.getByText(/inv\.pdf.*process a PDF on its own/)).toBeInTheDocument();
    unmount();

    render(<Harness initial={[pdf("inv.pdf", 1)]} />);
    drop(screen.getByTestId("photo-list"), [img("p1.jpg", 2)]);
    expect(order()).toBe("inv.pdf");
    expect(screen.getByText(/already holds a PDF/)).toBeInTheDocument();
  });

  it("accepts a single PDF dropped on the empty area, and not a PDF with photos in one drop", () => {
    const { unmount } = render(<Harness />);
    drop(screen.getByTestId("dropzone"), [pdf("inv.pdf")]);
    expect(order()).toBe("inv.pdf");
    unmount();
    render(<Harness />);
    drop(screen.getByTestId("dropzone"), [img("p1.jpg", 1), pdf("inv.pdf", 2)]);
    expect(order()).toBe("p1.jpg");
  });

  it("stops a stray file drop beside the list from opening the file, only while mounted", () => {
    const stray = () => {
      const event = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "dataTransfer", { value: { types: ["Files"], files: [] } });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    const textDrag = () => {
      const event = new Event("dragover", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "dataTransfer", { value: { types: ["text/plain"] } });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    const { unmount } = render(<Harness initial={[img("p1.jpg", 1)]} />);
    expect(stray()).toBe(true);
    expect(textDrag()).toBe(false);              // only file drags are touched
    expect(order()).toBe("p1.jpg");               // a stray drop adds nothing
    unmount();
    expect(stray()).toBe(false);
  });

  it("ignores drops while processing", () => {
    render(<Harness initial={[img("p1.jpg", 1)]} disabled />);
    drop(screen.getByTestId("photo-list"), [img("p2.jpg", 2)]);
    expect(order()).toBe("p1.jpg");
    expect(within(screen.getByTestId("photo-list")).queryByRole("button")).toBeNull();
  });
});
