/**
 * Axios client for the Invoice Intelligence API.
 *
 * The frontend's ONLY integration point with the backend. The response
 * interceptor unwraps the platform's `{success, data, error}` envelope
 * failures into a typed ApiError so callers and TanStack Query see one
 * consistent error shape.
 */

import axios, { AxiosError } from "axios";

import type { ApiErrorDetail } from "./types";

export class ApiError extends Error {
  readonly errorCode: string;
  readonly statusCode: number;
  readonly detail: unknown;
  /** The backend's request id — the one thing support needs to find the log line. */
  readonly requestId: string | null;

  constructor(statusCode: number, errorDetail: ApiErrorDetail | null, requestId: string | null = null) {
    super(errorDetail?.message ?? "Unexpected API error.");
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.errorCode = errorDetail?.error_code ?? "ERR_UNKNOWN";
    this.detail = errorDetail?.detail ?? null;
    this.requestId = requestId;
  }

  /** A message safe to show a person: what failed and where, plus a reference. */
  get userMessage(): string {
    const stage =
      this.detail && typeof this.detail === "object" && "stage" in this.detail
        ? String((this.detail as { stage: unknown }).stage)
        : null;
    const what =
      this.errorCode === "ERR_INTERNAL"
        ? "Invoice processing failed unexpectedly. Nothing was changed; please try again."
        : this.message;
    const where = stage && !what.toLowerCase().includes(stage) ? ` (stage: ${stage})` : "";
    const ref = this.requestId ? ` Reference ${this.requestId.slice(0, 8)}.` : "";
    return `${what}${where}${ref}`;
  }
}

/** The upload endpoint's own timeout. A spun-down Render free instance
 * delays the request before the application ever sees it — measured at
 * ~24s on the first pilot upload, and Render documents 50s or more — on
 * top of the ~5s the server then takes to store the file and return 202.
 * Only this endpoint waits that long; every other call keeps the 30s
 * default, so a genuinely dead backend still fails fast. */
export const PROCESS_TIMEOUT_MS = 90_000;

const PROCESS_PATH = "/invoices/process";

/** A timeout on the upload is not proof of failure: the invoice may already
 * have been stored and processed. Never tell the operator to just retry —
 * that is how duplicates get made. */
export const PROCESS_TIMEOUT_MESSAGE =
  "Processing is taking longer than expected. Your invoice may already have been received. " +
  "Check the invoice list before trying again.";

const TIMEOUT_MESSAGE =
  "The server took too long to respond. The request was not retried — check before sending it again.";

const NETWORK_MESSAGE = "Cannot reach the backend API. Is the server running?";

/** Axios reports a client-side timeout as ECONNABORTED (and ETIMEDOUT on
 * some adapters); neither means the server was unreachable. */
function isTimeout(error: AxiosError): boolean {
  return error.code === "ECONNABORTED" || error.code === "ETIMEDOUT";
}

export const apiClient = axios.create({
  baseURL: "/api/v1",
  timeout: 30_000,
  // The session lives in an httpOnly cookie set by POST /auth/login —
  // never read or attached by this client's own code — so every request
  // must ask the browser to send it.
  withCredentials: true,
});

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ error?: ApiErrorDetail; request_id?: string }>) => {
    if (error.response) {
      const requestId =
        error.response.data?.request_id ??
        (error.response.headers?.["x-request-id"] as string | undefined) ??
        null;
      throw new ApiError(error.response.status, error.response.data?.error ?? null, requestId);
    }
    // No response at all. Giving up on our side and never being heard are
    // different failures and must not share one message.
    if (isTimeout(error)) {
      throw new ApiError(0, {
        error_code: "ERR_TIMEOUT",
        message: error.config?.url?.includes(PROCESS_PATH) ? PROCESS_TIMEOUT_MESSAGE : TIMEOUT_MESSAGE,
      });
    }
    throw new ApiError(0, {
      error_code: "ERR_NETWORK",
      message: NETWORK_MESSAGE,
    });
  },
);
