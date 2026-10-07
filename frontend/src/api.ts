type TokenStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const KEY = "supportops.session.v1";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
    public diagnostics?: unknown,
  ) {
    super(message);
  }
}

export class Api {
  private generation = 0;
  onExpired: () => void = () => {};
  constructor(private storage: TokenStore = sessionStorage) {}
  hasToken() {
    return Boolean(this.storage.getItem(KEY));
  }
  setToken(token: string) {
    this.generation++;
    this.storage.setItem(KEY, token);
  }
  clear() {
    this.generation++;
    this.storage.removeItem(KEY);
  }

  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const generation = this.generation;
    const token = this.storage.getItem(KEY);
    let response: Response;
    try {
      response = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
    } catch {
      throw new ApiError(
        "NETWORK_ERROR",
        "无法连接 API，请检查后端是否正在运行。",
      );
    }
    // 拒绝旧组织的迟到结果，包含旧会话的 401。
    if (generation !== this.generation)
      throw new ApiError("STALE_SESSION", "会话已经切换。");
    if (response.status === 401) {
      this.clear();
      this.onExpired();
    }
    if (response.status === 204) return undefined as T;
    const data = await response.json();
    if (generation !== this.generation)
      throw new ApiError("STALE_SESSION", "会话已经切换。");
    if (!response.ok) {
      const error = data.error;
      const details = error?.details
        ?.map(
          (item: { field: string; type: string }) =>
            `${item.field} (${item.type})`,
        )
        .join("；");
      throw new ApiError(
        error?.code ?? "HTTP_ERROR",
        (error?.message ?? `请求失败 (${response.status})`) +
          (details ? `：${details}` : ""),
        response.status,
        data.usage
          ? { trace: data.trace, usage: data.usage, stage: data.stage }
          : undefined,
      );
    }
    return data as T;
  }
  async logout() {
    await this.request("/api/auth/logout", { method: "POST" });
    this.clear();
  }

  async actionEvents(
    path: string,
    after: number,
    signal: AbortSignal,
    receive: (value: {
      sequence: number;
      event: string;
      [key: string]: unknown;
    }) => void,
  ) {
    const generation = this.generation;
    const token = this.storage.getItem(KEY);
    const response = await fetch(path, {
      headers: {
        Authorization: `Bearer ${token ?? ""}`,
        "Last-Event-ID": String(after),
        Accept: "text/event-stream",
      },
      signal,
    });
    if (generation !== this.generation)
      throw new ApiError("STALE_SESSION", "会话已经切换。");
    if (response.status === 401) {
      this.clear();
      this.onExpired();
    }
    if (!response.ok || !response.body)
      throw new ApiError(
        "EVENT_STREAM_FAILED",
        "事件连接失败，请重新读取记录。",
        response.status,
      );
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (generation !== this.generation)
          throw new ApiError("STALE_SESSION", "会话已经切换。");
        if (signal.aborted) return;
        buffer += decoder.decode(value, { stream: !done });
        let end: number;
        while ((end = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          const event = block
            .split("\n")
            .find((line) => line.startsWith("event: "))
            ?.slice(7);
          const data = block
            .split("\n")
            .find((line) => line.startsWith("data: "))
            ?.slice(6);
          if (event === "stream_error")
            throw new ApiError(
              "EVENT_STREAM_EXPIRED",
              "事件连接的登录或范围已失效。",
            );
          if (event === "action_event" && data) receive(JSON.parse(data));
        }
        if (done) return;
      }
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
  }
}
