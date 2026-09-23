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
    throw new ApiError(0, {
      error_code: "ERR_NETWORK",
      message: "Cannot reach the backend API. Is the server running?",
    });
  },
);
