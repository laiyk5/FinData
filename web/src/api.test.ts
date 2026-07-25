import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  TOKEN_KEY,
  clearToken,
  getSystemStatus,
  getToken,
  handleUnauthorized,
  setToken,
} from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const STATUS_BODY = {
  status: "running",
  pid: 1,
  tasks: 0,
  running_tasks: 0,
  queue_lengths: {},
};

describe("api client", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("injects the Authorization header from sessionStorage", async () => {
    setToken("secret-token");
    const fetchMock = vi.fn(async () => jsonResponse(200, STATUS_BODY));
    vi.stubGlobal("fetch", fetchMock);

    await getSystemStatus();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/v1/system/status");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer secret-token");
  });

  it("throws ApiError with status and message from the error JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(409, { error: "dataset is busy" })),
    );

    const err = await getSystemStatus().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("dataset is busy");
  });

  it("falls back to a generic message when the body has no error field", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(500, {})));
    const err = await getSystemStatus().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).message).toBe("HTTP 500");
  });

  it("clears the stored token on a 401 response", async () => {
    setToken("stale-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(401, { error: "unauthorized" })),
    );

    const err = await getSystemStatus().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("handleUnauthorized clears the token and notifies listeners", () => {
    setToken("stale-token");
    const listener = vi.fn();
    window.addEventListener("findata:unauthorized", listener);
    try {
      handleUnauthorized();
      expect(getToken()).toBeNull();
      expect(listener).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("findata:unauthorized", listener);
    }
    clearToken();
  });
});
