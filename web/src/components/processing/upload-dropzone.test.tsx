import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { UploadDropzone } from "./upload-dropzone";

/**
 * The intake list: one invoice, several photos, in order. Adding,
 * removing, reordering, and the numbering the extraction will report.
 */

function png(name: string, at = 1) {
  return new File([new Uint8Array(2048)], name, { type: "image/png", lastModified: at });
}

function Harness({ initial = [] as File[] }) {
  const [files, setFiles] = useState<File[]>(initial);
  return (
    <>
      <UploadDropzone files={files} onFilesChange={setFiles} />
      <output data-testid="order">{files.map((f) => f.name).join(",")}</output>
    </>
  );
}

describe("multi-photo intake", () => {
  it("starts empty, then lists photos in the order they were added, numbered", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.getByTestId("dropzone")).toBeInTheDocument();
    await user.upload(screen.getByTestId("file-input"), [png("IMG_001.jpg", 1), png("IMG_002.jpg", 2)]);
    const rows = screen.getAllByTestId("photo-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("1");
    expect(rows[0]).toHaveTextContent("IMG_001.jpg");
    expect(rows[1]).toHaveTextContent("2");
    expect(rows[1]).toHaveTextContent("IMG_002.jpg");
    expect(screen.getByTestId("order")).toHaveTextContent("IMG_001.jpg,IMG_002.jpg");
    expect(screen.getByTestId("add-photo")).toHaveTextContent("Add photo");
  });

  it("adds another photo to an existing list and refuses the same one twice", async () => {
    const user = userEvent.setup();
    render(<Harness initial={[png("a.jpg", 1)]} />);
    expect(screen.getByTestId("add-photo")).toHaveTextContent("Add another photo of this invoice");
    await user.upload(screen.getByTestId("file-input"), [png("a.jpg", 1), png("b.jpg", 2)]);
    expect(screen.getAllByTestId("photo-row")).toHaveLength(2);
    expect(screen.getByText(/"a.jpg" is already in the list/)).toBeInTheDocument();
  });

  it("removes a photo before processing", async () => {
    const user = userEvent.setup();
    render(<Harness initial={[png("a.jpg", 1), png("b.jpg", 2), png("c.jpg", 3)]} />);
    await user.click(screen.getByRole("button", { name: "Remove b.jpg" }));
    expect(screen.getByTestId("order")).toHaveTextContent("a.jpg,c.jpg");
    expect(screen.getAllByTestId("photo-row")[1]).toHaveTextContent("2");   // renumbered
  });

  it("reorders photos, with the ends pinned", async () => {
    const user = userEvent.setup();
    render(<Harness initial={[png("a.jpg", 1), png("b.jpg", 2), png("c.jpg", 3)]} />);
    expect(screen.getByRole("button", { name: "Move a.jpg up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move c.jpg down" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Move c.jpg up" }));
    expect(screen.getByTestId("order")).toHaveTextContent("a.jpg,c.jpg,b.jpg");
    await user.click(screen.getByRole("button", { name: "Move a.jpg down" }));
    expect(screen.getByTestId("order")).toHaveTextContent("c.jpg,a.jpg,b.jpg");
  });

  it("rejects an unsupported dropped type and keeps the rest", () => {
    render(<Harness />);
    const txt = new File([new Uint8Array(2048)], "notes.txt", { type: "text/plain" });
    fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [txt, png("ok.jpg")] } });
    expect(screen.getAllByTestId("photo-row")).toHaveLength(1);
    expect(screen.getByText(/notes\.txt.*not a PDF, PNG, or JPEG/)).toBeInTheDocument();
  });

  it("locks the list while processing", () => {
    render(<UploadDropzone files={[png("a.jpg")]} onFilesChange={() => {}} disabled />);
    expect(within(screen.getByTestId("photo-list")).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("add-photo")).not.toBeInTheDocument();
  });
});
