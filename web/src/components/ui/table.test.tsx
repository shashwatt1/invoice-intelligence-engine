import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Table, TableBody, TableCell, TableRow } from "./table";

function body() {
  return <TableBody><TableRow><TableCell>cell</TableCell></TableRow></TableBody>;
}

describe("Table container", () => {
  it("is unchanged for callers that pass no container props", () => {
    const { container } = render(<Table className="extra-table">{body()}</Table>);
    const wrapper = container.querySelector("[data-slot=table-container]")!;
    expect(wrapper.getAttribute("class")).toBe("relative w-full overflow-x-auto");
    expect(wrapper.hasAttribute("tabindex")).toBe(false);
    expect(wrapper.hasAttribute("role")).toBe(false);
    expect(wrapper.hasAttribute("aria-label")).toBe(false);
    expect(container.querySelector("table")).toHaveClass("w-full", "caption-bottom", "text-sm", "extra-table");
  });

  it("applies opt-in container props, merging classes", () => {
    const { container } = render(
      <Table containerProps={{ tabIndex: 0, role: "region", "aria-label": "Rows", className: "focus-visible:outline-2" }}>
        {body()}
      </Table>,
    );
    const wrapper = container.querySelector("[data-slot=table-container]")!;
    expect(wrapper).toHaveClass("relative", "w-full", "overflow-x-auto", "focus-visible:outline-2");
    expect(wrapper).toHaveAttribute("tabindex", "0");
    expect(wrapper).toHaveAttribute("role", "region");
    expect(wrapper).toHaveAttribute("aria-label", "Rows");
  });
});
