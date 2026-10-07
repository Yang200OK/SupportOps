import { afterEach, expect, test, vi } from "vitest";
import { Api } from "../src/api";

function api() {
  const storage = new Map<string, string>();
  const value = new Api({
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, token) => {
      storage.set(key, token);
    },
    removeItem: (key) => {
      storage.delete(key);
    },
  });
  value.setToken("test-only-session");
  return value;
}
afterEach(() => vi.unstubAllGlobals());

test("REQ-1406: 分段 UTF-8 事件正确重组，认证和游标仅放请求头", async () => {
  const client = api();
  const data = new TextEncoder().encode(
    'id: 4\nevent: action_event\ndata: {"sequence":4,"event":"人工批准"}\n\n',
  );
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(data.slice(0, 58));
      controller.enqueue(data.slice(58));
      controller.close();
    },
  });
  const fetch = vi.fn().mockResolvedValue(new Response(stream));
  vi.stubGlobal("fetch", fetch);
  const receive = vi.fn();
  await client.actionEvents(
    "/api/actions/id/events",
    3,
    new AbortController().signal,
    receive,
  );
  expect(receive).toHaveBeenCalledExactlyOnceWith({
    sequence: 4,
    event: "人工批准",
  });
  expect(fetch.mock.calls[0][0]).toBe("/api/actions/id/events");
  expect(fetch.mock.calls[0][1].headers).toMatchObject({
    "Last-Event-ID": "3",
    Authorization: "Bearer test-only-session",
  });
});

test("REQ-1406: 旧会话的迟到事件不进入新会话", async () => {
  const client = api();
  let resolve!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockReturnValue(
      new Promise<Response>((done) => {
        resolve = done;
      }),
    ),
  );
  const receive = vi.fn();
  const pending = client.actionEvents(
    "/api/actions/id/events",
    0,
    new AbortController().signal,
    receive,
  );
  client.setToken("new-session");
  resolve(
    new Response(
      'event: action_event\ndata: {"sequence":1,"event":"旧事件"}\n\n',
    ),
  );
  await expect(pending).rejects.toMatchObject({ code: "STALE_SESSION" });
  expect(receive).not.toHaveBeenCalled();
});

test("REQ-1406: 事件会话失效不自动重连", async () => {
  const client = api();
  const fetch = vi
    .fn()
    .mockResolvedValue(new Response("event: stream_error\ndata: {}\n\n"));
  vi.stubGlobal("fetch", fetch);
  await expect(
    client.actionEvents(
      "/api/actions/id/events",
      0,
      new AbortController().signal,
      vi.fn(),
    ),
  ).rejects.toMatchObject({ code: "EVENT_STREAM_EXPIRED" });
  expect(fetch).toHaveBeenCalledOnce();
});
