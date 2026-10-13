import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InvoicePrice } from "./invoice-price";

describe("InvoicePrice", () => {
  it("labels source values as the invoice's and the derived figure as calculated", () => {
    render(<InvoicePrice quantity={2} unitPrice={30} lineTotal={60} unitDiscount={0.5} unitsPerCase={24} unitsLabel="confirmed" />);
    expect(screen.getByTestId("invoice-price")).toHaveTextContent("From invoice: qty 2 · $30.00 each · line $60.00 · discount $0.50/unit");
    expect(screen.getByTestId("invoice-price-per-unit")).toHaveTextContent("≈ $1.25 per selling unit at 24/case (calculated, confirmed)");
  });

  it("says so when the invoice gave no unit price and calculates nothing", () => {
    render(<InvoicePrice quantity={3} unitPrice={null} lineTotal={null} unitsPerCase={12} />);
    expect(screen.getByTestId("invoice-price")).toHaveTextContent("qty 3 · no unit price on the invoice");
    expect(screen.queryByTestId("invoice-price-per-unit")).toBeNull();
  });

  it("calculates nothing without valid units per case", () => {
    render(<InvoicePrice quantity={1} unitPrice={18} lineTotal={18} unitsPerCase={null} />);
    expect(screen.queryByTestId("invoice-price-per-unit")).toBeNull();
  });
});
