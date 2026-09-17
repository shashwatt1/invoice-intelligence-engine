import { describe, expect, it } from "vitest";

import { ApiError } from "./client";

/** What a person sees when a request fails: what and where, a reference, never internals. */
describe("ApiError.userMessage", () => {
  it("names the stage and the request reference for a typed backend error", () => {
    const error = new ApiError(503, {
      error_code: "ERR_DATABASE_FAILURE",
      message: "Invoice processing failed while recording the upload. Nothing was processed; the files can be uploaded again.",
      detail: { stage: "intake", photo_count: 2 },
    }, "2e539afb-1c9f-424b-a502-318a578d1d22");
    expect(error.userMessage).toBe(
      "Invoice processing failed while recording the upload. Nothing was processed; the files can be uploaded again. (stage: intake) Reference 2e539afb.",
    );
  });

  it("replaces the bare internal-error text and never carries a traceback", () => {
    const error = new ApiError(500, {
      error_code: "ERR_INTERNAL",
      message: "An unexpected internal error occurred. Please contact support.",
    }, "abcdef12-0000-0000-0000-000000000000");
    expect(error.userMessage).toBe(
      "Invoice processing failed unexpectedly. Nothing was changed; please try again. Reference abcdef12.",
    );
    expect(error.userMessage).not.toMatch(/Traceback|sqlalchemy|\/Users\//);
  });

  it("works without a reference", () => {
    const error = new ApiError(409, { error_code: "ERR_DUPLICATE_DOCUMENT", message: "Already processed." });
    expect(error.userMessage).toBe("Already processed.");
  });
});
