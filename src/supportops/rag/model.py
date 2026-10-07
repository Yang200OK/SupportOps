"""复用已有 HTTPS 传输；真实回答与原固定模型探测相互独立。"""

import json

from pydantic import Field, ValidationError

from supportops.models.provider import ModelFailure, ModelSettings, integer


class AnswerModelSettings(ModelSettings):
    # 非流式回答比原固定探测更长；本轮显式上限 60 秒，仍允许环境配置缩短。
    timeout_seconds: float = Field(default=60, ge=1, le=60, allow_inf_nan=False)


class ChatFailure(ModelFailure):
    def __init__(self, code, usage):
        super().__init__(code)
        self.usage = usage


def chat_json(provider, system, data, schema, max_tokens, *, role="main", image_url=None):
    settings = provider.settings
    if role not in ("main", "light"):
        raise ValueError("只允许应用显式指定主模型或轻模型。")
    model = settings.main_model if role == "main" else settings.light_model
    parameters = {"temperature": 0, "max_tokens": max_tokens, "enable_thinking": False}
    response, latency = provider.post(
        settings.base_url + "/chat/completions",
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system
                    + "\nJSON Schema:\n"
                    + json.dumps(schema.model_json_schema(), ensure_ascii=False),
                },
                {
                    "role": "user",
                    "content": json.dumps(data, ensure_ascii=False)
                    if image_url is None
                    else [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": json.dumps(data, ensure_ascii=False)},
                    ],
                },
            ],
            **parameters,
            "response_format": {"type": "json_object"},
        },
    )
    usage = None
    try:
        incoming = integer(response["usage"]["prompt_tokens"])
        outgoing = integer(response["usage"]["completion_tokens"])
        if integer(response["usage"]["total_tokens"]) != incoming + outgoing:
            raise ValueError("用量不一致。")
        returned = response["model"]
        if type(returned) is not str or returned != model:
            raise ValueError("返回模型与显式主模型不一致。")
        usage = {
            "requested_model": model,
            "returned_model": returned,
            "input_tokens": incoming,
            "output_tokens": outgoing,
            "latency_ms": latency,
            "parameters": parameters,
            "timeout_seconds": settings.timeout_seconds,
            "cost_cny": None,
        }
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("必须返回一份完整回答。")
        choice = choices[0]
        if choice["finish_reason"] != "stop" or choice["message"].get("tool_calls"):
            raise ValueError("输出未完整结束或试图调用工具。")
        content = choice["message"]["content"]
        if type(content) is not str or len(content) > 60000:
            raise ValueError("输出文本不符合预算。")
        result = schema.model_validate_json(content)
    except (KeyError, IndexError, TypeError, ValueError, ValidationError):
        raise ChatFailure("MODEL_RESPONSE_INVALID", usage) from None
    return result, usage
