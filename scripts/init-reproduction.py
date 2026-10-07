"""为独立复现生成私有配置；不用旧凭据、端口或数据卷。"""

import argparse
import json
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def create(root, name, api_port, web_port):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name) or name in {
        "con",
        "prn",
        "aux",
        "nul",
        *[f"com{i}" for i in range(1, 10)],
        *[f"lpt{i}" for i in range(1, 10)],
    }:
        raise ValueError("复现名称必须为非保留的小写字母 / 数字 / 连字符。")
    ports = (api_port, web_port)
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports):
        raise ValueError("必须显式指定两个有效端口。")
    if api_port == web_port or set(ports) & {8010, 5173, 8101, 8102, 55432, 15432}:
        raise ValueError("端口必须独立且不同于既有业务或实验端口。")
    root = Path(root).resolve()
    relative = Path("local/reproduction") / name
    destination = root / relative
    if not destination.resolve().is_relative_to(root / "local/reproduction"):
        raise ValueError("私有目录越界。")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    admin, app = secrets.token_hex(24), secrets.token_hex(24)
    values = {
        "SUPPORTOPS_POSTGRES_PASSWORD": admin,
        "SUPPORTOPS_APP_PASSWORD": app,
        "SUPPORTOPS_REPRO_DIRECTORY": "./" + relative.as_posix(),
        "SUPPORTOPS_REPRO_API_PORT": str(api_port),
        "SUPPORTOPS_REPRO_WEB_PORT": str(web_port),
        "SUPPORTOPS_MODEL_MAIN_MODEL": "qwen3.6-plus",
        "SUPPORTOPS_MODEL_VISION_MODEL": "qwen-vl-plus",
    }
    # 两套库仍使用原应用允许的名称，隔离由新的容器 / 网络 / 数据卷完成。
    for prefix, database, role, password in [
        ("", "supportops", "supportops_app", app),
        ("ADMIN_", "supportops", "supportops_admin", admin),
        ("TEST_", "supportops_test", "supportops_app", app),
        ("TEST_ADMIN_", "supportops_test", "supportops_admin", admin),
    ]:
        values[f"SUPPORTOPS_{prefix}DATABASE_URL"] = (
            f"postgresql+psycopg://{role}:{password}@postgres:5432/{database}"
        )
    config = destination / ".env"
    with config.open("x", encoding="utf-8", newline="\n") as file:
        file.write("".join(f"{k}={v}\n" for k, v in values.items()))
    config.chmod(0o600)
    result = {
        "schema_version": "supportops.reproduction.v1",
        "name": name,
        "project": "supportops-repro-" + name,
        "env_file": (relative / ".env").as_posix(),
        "api_port": api_port,
        "web_port": web_port,
        "model_calls": 0,
    }
    (destination / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--api-port", required=True, type=int)
    parser.add_argument("--web-port", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(create(ROOT, args.name, args.api_port, args.web_port), ensure_ascii=False))


if __name__ == "__main__":
    main()
