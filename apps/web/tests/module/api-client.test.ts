import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "@/lib/api/client";
import { getDisplayErrorMessage } from "@/lib/api/errors";

describe("ApiClient", () => {
  let client: ApiClient;

  beforeEach(() => {
    client = new ApiClient("http://api.example.test");
  });

  it("adds bearer and active organization headers to requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: "notebook-1" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    client.setAuthContext({
      getToken: async () => "token-123",
      getActiveOrganizationId: () => "org_123",
    });

    await client.listNotebooks();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers).toMatchObject({
      Authorization: "Bearer token-123",
      "X-Active-Organization-Id": "org_123",
    });
    expect(options.credentials).toBe("include");
  });

  it("parses the standardized API error shape", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "source_not_ready",
          message: "The source is still indexing.",
          retryable: true,
          provider: "qdrant",
          details: { source_id: "src_123" },
        }),
        {
          status: 409,
          headers: { "Content-Type": "application/json" },
        },
      ),
    ));

    await expect(client.listSources("notebook-1")).rejects.toMatchObject({
      message: "The source is still indexing.",
      status: 409,
      code: "source_not_ready",
      retryable: true,
      provider: "qdrant",
      details: { source_id: "src_123" },
    });
  });

  it("falls back to FastAPI detail strings when the standardized shape is absent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Missing Clerk session token" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    await expect(client.getCurrentUser()).rejects.toMatchObject({
      message: "Missing Clerk session token",
      status: 401,
    });
  });

  it("formats retryable provider errors for the UI", () => {
    const error = new ApiError("The source is still indexing.", 409, {
      code: "source_not_ready",
      retryable: true,
      provider: "qdrant",
    });

    expect(getDisplayErrorMessage(error, "fallback")).toBe(
      "The source is still indexing. Provider: qdrant. Code: source_not_ready. This request can be retried.",
    );
  });
});
