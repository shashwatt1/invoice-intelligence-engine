import { AxiosError, type AxiosAdapter } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiClient, ApiError, PROCESS_TIMEOUT_MESSAGE, PROCESS_TIMEOUT_MS } from "./client";
import { getInvoice, processInvoice } from "./endpoints";

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

/**
 * Giving up on our side and never being heard are different failures. The
 * first pilot upload proved why it matters: a cold-started instance delayed
 * the request ~24s before the server even began, the upload then succeeded
 * in full, and the operator was told the backend was unreachable — so they
 * uploaded the same invoice again.
 */
describe("apiClient failure classification", () => {
  const original = apiClient.defaults.adapter;
  afterEach(() => {
    apiClient.defaults.adapter = original;
  });

  const invoicePhoto = () => new File(["x".repeat(2048)], "IMG_6569.jpg", { type: "image/jpeg" });

  /** Drives the real interceptor chain — no transport, no new dependency. */
  const adapter = (handler: AxiosAdapter) => {
    const spy = vi.fn(handler);
    apiClient.defaults.adapter = spy as unknown as AxiosAdapter;
    return spy;
  };

  it("tells the operator an upload may already have been received when it times out", async () => {
    adapter(async (config) => {
      throw new AxiosError("timeout of 90000ms exceeded", "ECONNABORTED", config);
    });

    const error = await processInvoice([invoicePhoto()], null).catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error.errorCode).toBe("ERR_TIMEOUT");
    expect(error.userMessage).toBe(PROCESS_TIMEOUT_MESSAGE);
    expect(error.userMessage).not.toMatch(/Cannot reach|server running/);
  });

  it("still reports a genuine connection failure as the backend being unreachable", async () => {
    adapter(async (config) => {
      throw new AxiosError("Network Error", AxiosError.ERR_NETWORK, config);
    });

    const error = await processInvoice([invoicePhoto()], null).catch((e) => e);

    expect(error.errorCode).toBe("ERR_NETWORK");
    expect(error.userMessage).toBe("Cannot reach the backend API. Is the server running?");
  });

  it("leaves a normal HTTP error untouched — a duplicate is still a duplicate", async () => {
    adapter(async (config) => {
      throw new AxiosError("Request failed with status code 409", "ERR_BAD_REQUEST", config, null, {
        status: 409,
        statusText: "Conflict",
        headers: {},
        config,
        data: {
          error: {
            error_code: "ERR_DUPLICATE_DOCUMENT",
            message: "This document has already been uploaded and processed.",
            detail: { existing_document_id: "f6f714c0-a326-42df-adbb-eee6ab8d1391" },
          },
          request_id: "0682e838-8bf3-4f3b-81b2-4b9a2b81dec3",
        },
      });
    });

    const error = await processInvoice([invoicePhoto()], null).catch((e) => e);

    expect(error.statusCode).toBe(409);
    expect(error.errorCode).toBe("ERR_DUPLICATE_DOCUMENT");
    expect(error.requestId).toBe("0682e838-8bf3-4f3b-81b2-4b9a2b81dec3");
    expect((error.detail as { existing_document_id: string }).existing_document_id).toBe(
      "f6f714c0-a326-42df-adbb-eee6ab8d1391",
    );
  });

  it("returns the 202 body on success", async () => {
    adapter(async (config) => ({
      status: 202,
      statusText: "Accepted",
      headers: {},
      config,
      data: {
        success: true,
        data: {
          document_id: "f6f714c0-a326-42df-adbb-eee6ab8d1391",
          filename: "IMG_6569.jpg",
          status: "UPLOADED",
          status_url: "/api/v1/documents/f6f714c0-a326-42df-adbb-eee6ab8d1391",
        },
      },
    }));

    const accepted = await processInvoice([invoicePhoto()], null);

    expect(accepted.document_id).toBe("f6f714c0-a326-42df-adbb-eee6ab8d1391");
    expect(accepted.filename).toBe("IMG_6569.jpg");
  });

  it("never retries a timed-out upload — a retry is how the duplicate got made", async () => {
    const sent = adapter(async (config) => {
      throw new AxiosError("timeout exceeded", "ECONNABORTED", config);
    });

    await processInvoice([invoicePhoto()], null).catch(() => undefined);

    expect(sent).toHaveBeenCalledTimes(1);
  });

  it("waits longer only for the upload; other calls keep the fast default", async () => {
    const sent = adapter(async (config) => ({
      status: 200, statusText: "OK", headers: {}, config, data: { success: true, data: {} },
    }));

    await processInvoice([invoicePhoto()], null);
    expect(sent.mock.calls[0][0].timeout).toBe(PROCESS_TIMEOUT_MS);

    await getInvoice("f6f714c0-a326-42df-adbb-eee6ab8d1391");
    expect(sent.mock.calls[1][0].timeout).toBe(30_000);
  });
});
