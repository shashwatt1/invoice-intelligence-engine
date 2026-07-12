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

  constructor(statusCode: number, errorDetail: ApiErrorDetail | null) {
    super(errorDetail?.message ?? "Unexpected API error.");
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.errorCode = errorDetail?.error_code ?? "ERR_UNKNOWN";
    this.detail = errorDetail?.detail ?? null;
  }
}

export const apiClient = axios.create({
  baseURL: "/api/v1",
  timeout: 30_000,
});

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ error?: ApiErrorDetail }>) => {
    if (error.response) {
      throw new ApiError(error.response.status, error.response.data?.error ?? null);
    }
    throw new ApiError(0, {
      error_code: "ERR_NETWORK",
      message: "Cannot reach the backend API. Is the server running?",
    });
  },
);
