"""固定构造输入验证模型契约，错误不回显服务商正文或密钥。"""

import json
import logging
import math
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from supportops.settings import ROOT


class ModelFailure(Exception):
    def __init__(self, code: str, http_status: int | None = None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUPPORTOPS_MODEL_",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )
    api_key: SecretStr | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("SUPPORTOPS_MODEL_API_KEY", "DASHSCOPE_API_KEY"),
    )
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    rerank_url: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )
    main_model: str = "qwen3.6-plus"
    light_model: str = "qwen3.8-flash"
    embedding_model: str = "qwen3.7-text-embedding"
    rerank_model: str = "qwen3.7-text-rerank"
    embedding_dimensions: int = Field(default=1024, ge=1, le=4096)
    timeout_seconds: float = Field(default=30, ge=1, le=60, allow_inf_nan=False)

    @field_validator("api_key", mode="before")
    @classmethod
    def empty_key(cls, value):
        return None if value == "" else value

    @field_validator("base_url", "rerank_url")
    @classmethod
    def provider_https_only(cls, value):
        url = urlsplit(value)
        host = url.hostname or ""
        allowed = host in (
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
        ) or host.endswith(".maas.aliyuncs.com")
        if (
            url.scheme != "https"
            or not allowed
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.port not in (None, 443)
        ):
            raise ValueError("只能使用显式的百炼 HTTPS 接口，不能携带 URL 凭据。")
        return value.rstrip("/")


def integer(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("使用量必须是非负整数。")
    return value


class Provider:
    def __init__(self, settings: ModelSettings, transport=None):
        if settings.api_key is None:
            raise ModelFailure("MODEL_NOT_CONFIGURED")
        self.settings = settings
        # 测试传输只由测试显式注入；真实请求不重试、不跟随重定向、不换服务。
        self.client = httpx.Client(
            timeout=settings.timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
            headers={"Authorization": "Bearer " + settings.api_key.get_secret_value()},
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def post(self, url: str, payload: dict) -> tuple[dict, int]:
        start = perf_counter()
        try:
            response = self.client.post(url, json=payload)
        except httpx.TimeoutException as failure:
            # 只记录异常类型和显式上限，用于区分连接与读取；不记录请求或凭据。
            logging.getLogger(__name__).warning(
                "模型超时类型=%s；配置上限=%s秒",
                type(failure).__name__,
                self.settings.timeout_seconds,
            )
            raise ModelFailure("MODEL_TIMEOUT") from None
        except httpx.RequestError:
            raise ModelFailure("MODEL_CONNECTION_FAILED") from None
        if response.status_code != 200:
            raise ModelFailure("MODEL_HTTP_ERROR", response.status_code)
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("响应必须是对象。")
        except (ValueError, TypeError):
            raise ModelFailure("MODEL_RESPONSE_INVALID") from None
        return result, round((perf_counter() - start) * 1000)

    def chat(self, role: str) -> dict:
        if role not in ("main", "light"):
            raise ValueError("只允许显式主模型或轻任务模型。")
        model = self.settings.main_model if role == "main" else self.settings.light_model
        body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        '这只是接口契约探测。仅返回 JSON：{"probe_status":"ok",'
                        '"root_cause":null}。不得推断根因。'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "构造输入：RelayDesk 报告 RD_TIMEOUT，但没有日志或证据。请输出指定 JSON。"
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 128,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }
        response, latency = self.post(self.settings.base_url + "/chat/completions", body)
        try:
            choice = response["choices"][0]
            content = json.loads(choice["message"]["content"])
            if (
                content != {"probe_status": "ok", "root_cause": None}
                or choice["finish_reason"] != "stop"
            ):
                raise ValueError("探测 JSON 或结束原因不符合契约。")
            input_tokens = integer(response["usage"]["prompt_tokens"])
            output_tokens = integer(response["usage"]["completion_tokens"])
            if integer(
                response["usage"]["total_tokens"]
            ) != input_tokens + output_tokens or not isinstance(response["model"], str):
                raise ValueError("模型身份或使用量不符合契约。")
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelFailure("MODEL_RESPONSE_INVALID") from None
        return {
            "role": role,
            "requested_model": model,
            "returned_model": response["model"],
            "probe_status": "ok",
            "latency_ms": latency,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cny": None,
            "parameters": {"temperature": 0, "max_tokens": 128, "enable_thinking": False},
        }

    def embedding(self) -> dict:
        response, latency = self.post(
            self.settings.base_url + "/embeddings",
            {
                "model": self.settings.embedding_model,
                "input": ["RelayDesk 投递超时 RD_TIMEOUT", "请补充产品版本"],
                "dimensions": self.settings.embedding_dimensions,
                "encoding_format": "float",
            },
        )
        try:
            data = response["data"]
            indices = [integer(item["index"]) for item in data]
            if sorted(indices) != [0, 1]:
                raise ValueError("向量输入输出索引必须一一对应。")
            ordered = sorted(data, key=lambda item: item["index"])
            for item in ordered:
                vector = item["embedding"]
                if len(vector) != self.settings.embedding_dimensions or not all(
                    type(value) in (float, int) and math.isfinite(value) for value in vector
                ):
                    raise ValueError("向量维度或数值不符合契约。")
            tokens = integer(response["usage"]["prompt_tokens"])
        except (KeyError, TypeError, ValueError):
            raise ModelFailure("MODEL_RESPONSE_INVALID") from None
        return {
            "role": "embedding",
            "requested_model": self.settings.embedding_model,
            "indices": [0, 1],
            "dimensions": self.settings.embedding_dimensions,
            "latency_ms": latency,
            "input_tokens": tokens,
            "output_tokens": None,
            "cost_cny": None,
        }

    def embed_texts(self, texts: list[str]) -> dict:
        # 原固定探测接口保留；检索单独传入真实文本并返回实际向量。
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= 10
            or any(type(item) is not str or not item.strip() or len(item) > 6000 for item in texts)
        ):
            raise ValueError("每批需要 1 至 10 个非空文本，每项不超过 6000 字符。")
        response, latency = self.post(
            self.settings.base_url + "/embeddings",
            {
                "model": self.settings.embedding_model,
                "input": texts,
                "dimensions": self.settings.embedding_dimensions,
                "encoding_format": "float",
            },
        )
        try:
            data = response["data"]
            if not isinstance(data, list) or sorted(integer(row["index"]) for row in data) != list(
                range(len(texts))
            ):
                raise ValueError("批次响应索引不完整。")
            vectors = [row["embedding"] for row in sorted(data, key=lambda row: row["index"])]
            for vector in vectors:
                if (
                    not isinstance(vector, list)
                    or len(vector) != self.settings.embedding_dimensions
                ):
                    raise ValueError("向量维度不一致。")
                if not all(
                    type(x) in (int, float) and abs(x) <= 3.4e38 and math.isfinite(x)
                    for x in vector
                ) or not any(x != 0 for x in vector):
                    raise ValueError("向量必须为有限、非零且可保存的数值。")
            # cosine 不受正比例缩放影响；先缩放再归一化，避免 float32 上溢或下溢。
            normalized = []
            for vector in vectors:
                scale = max(abs(x) for x in vector)
                scaled = [x / scale for x in vector]
                norm = math.sqrt(sum(x * x for x in scaled))
                normalized.append([x / norm for x in scaled])
            tokens = integer(response["usage"]["prompt_tokens"])
            returned = response.get("model")
            if returned is not None and (not isinstance(returned, str) or not returned.strip()):
                raise ValueError("返回模型身份无效。")
        except (KeyError, TypeError, ValueError):
            raise ModelFailure("MODEL_RESPONSE_INVALID") from None
        return {
            "vectors": normalized,
            "requested_model": self.settings.embedding_model,
            "returned_model": returned,
            "dimensions": self.settings.embedding_dimensions,
            "input_tokens": tokens,
            "latency_ms": latency,
            "cost_cny": None,
        }

    def rerank_texts(self, query: str, texts: list[str]) -> dict:
        # 固定探测方法保留；真实排序只接收调用方已核对的有界文本池。
        if (
            type(query) is not str
            or not query.strip()
            or len(query) > 2000
            or not isinstance(texts, list)
            or not 1 <= len(texts) <= 40
            or any(type(text) is not str or not text.strip() or len(text) > 6000 for text in texts)
            or sum(len(text) for text in texts) > 60000
        ):
            raise ValueError("排序需要非空问题及有界文本池。")
        response, latency = self.post(
            self.settings.rerank_url,
            {
                "model": self.settings.rerank_model,
                "input": {"query": query, "documents": texts},
                "parameters": {"top_n": len(texts)},
            },
        )
        try:
            rows = response["output"]["results"]
            if not isinstance(rows, list):
                raise ValueError("排序结果必须是列表。")
            indices = [integer(row["index"]) for row in rows]
            scores = [row["relevance_score"] for row in rows]
            if sorted(indices) != list(range(len(texts))) or not all(
                type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1
                for score in scores
            ):
                raise ValueError("排序结果不能缺失、重复或引入新证据。")
            tokens = integer(response["usage"]["total_tokens"])
            if (
                "prompt_tokens" in response["usage"]
                and integer(response["usage"]["prompt_tokens"]) != tokens
            ):
                raise ValueError("排序用量不一致。")
            returned = response.get("model")
            if returned is not None and (type(returned) is not str or not returned.strip()):
                raise ValueError("模型身份无效。")
        except (KeyError, TypeError, ValueError):
            raise ModelFailure("MODEL_RESPONSE_INVALID") from None
        return {
            "indices": indices,
            "scores": scores,
            "requested_model": self.settings.rerank_model,
            "returned_model": returned,
            "input_tokens": tokens,
            "latency_ms": latency,
            "cost_cny": None,
        }

    def rerank(self) -> dict:
        response, latency = self.post(
            self.settings.rerank_url,
            {
                "model": self.settings.rerank_model,
                "input": {
                    "query": "RelayDesk RD_TIMEOUT 超时如何取证？",
                    "documents": [
                        "检查投递日志与下游响应耗时后再判断超时原因。",
                        "用户可以修改界面颜色。",
                    ],
                },
                "parameters": {"top_n": 2},
            },
        )
        try:
            rows = response["output"]["results"]
            indices = [integer(item["index"]) for item in rows]
            scores = [item["relevance_score"] for item in rows]
            if sorted(indices) != [0, 1] or not all(
                type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1
                for score in scores
            ):
                raise ValueError("排序索引或相关性分数不符合契约。")
            tokens = integer(response["usage"]["total_tokens"])
        except (KeyError, TypeError, ValueError):
            raise ModelFailure("MODEL_RESPONSE_INVALID") from None
        return {
            "role": "rerank",
            "requested_model": self.settings.rerank_model,
            "indices": indices,
            "scores": scores,
            "latency_ms": latency,
            "input_tokens": tokens,
            "output_tokens": None,
            "cost_cny": None,
        }
