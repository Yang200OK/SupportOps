import { afterEach, expect, test, vi } from "vitest";
import { Api, ApiError } from "../src/api";

function store() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}
afterEach(() => vi.unstubAllGlobals());

test("REQ-201/209: 当前会话发送认证，退出真实调用服务器并清除 token", async () => {
  const storage = store();
  const api = new Api(storage);
  api.setToken("test-token");
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetch);
  await api.logout();
  expect(fetch.mock.calls[0][0]).toBe("/api/auth/logout");
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe(
    "Bearer test-token",
  );
  expect(api.hasToken()).toBe(false);
});

test("REQ-209: 401 清除过期会话并通知界面", async () => {
  const api = new Api(store());
  api.setToken("test-token");
  const expired = vi.fn();
  api.onExpired = expired;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "UNAUTHORIZED", message: "会话失效" },
        }),
        { status: 401 },
      ),
    ),
  );
  await expect(api.request("/api/tickets")).rejects.toBeInstanceOf(ApiError);
  expect(api.hasToken()).toBe(false);
  expect(expired).toHaveBeenCalledOnce();
});

test("REQ-209: 旧组织的迟到响应不能进入新会话", async () => {
  const api = new Api(store());
  api.setToken("team-a");
  let resolve!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockReturnValue(
      new Promise<Response>((done) => {
        resolve = done;
      }),
    ),
  );
  const pending = api.request("/api/tickets");
  api.setToken("team-b");
  resolve(new Response(JSON.stringify({ items: [{ title: "旧组织工单" }] })));
  await expect(pending).rejects.toMatchObject({ code: "STALE_SESSION" });
  expect(api.hasToken()).toBe(true);
});

test("REQ-209: 旧会话 401 不会清除新会话", async () => {
  const api = new Api(store());
  api.setToken("team-a");
  let resolve!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockReturnValue(
      new Promise<Response>((done) => {
        resolve = done;
      }),
    ),
  );
  const pending = api.request("/api/tickets");
  api.setToken("team-b");
  resolve(new Response("{}", { status: 401 }));
  await expect(pending).rejects.toMatchObject({ code: "STALE_SESSION" });
  expect(api.hasToken()).toBe(true);
});

test("REQ-201: 显示字段错误，失败不伪造成功、不自动重试", async () => {
  const api = new Api(store());
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        error: {
          code: "REQUEST_VALIDATION_FAILED",
          message: "输入无效",
          details: [{ field: "title", type: "too_long" }],
        },
      }),
      { status: 422 },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  await expect(
    api.request("/api/tickets", { method: "POST", body: "{}" }),
  ).rejects.toMatchObject({
    code: "REQUEST_VALIDATION_FAILED",
    message: "输入无效：title (too_long)",
  });
  expect(fetch).toHaveBeenCalledOnce();
});
