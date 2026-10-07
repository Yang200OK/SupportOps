"""独立生成实验凭据，不读取或覆盖业务凭据。"""

import secrets

from supportops.settings import ROOT


def main():
    with (ROOT / ".env.lab").open("x", encoding="utf-8", newline="\n") as file:
        file.write("LAB_POSTGRES_PASSWORD=" + secrets.token_hex(24) + "\n")
        file.write("LAB_CONTROL_TOKEN=" + secrets.token_hex(24) + "\n")
    print("已生成独立 .env.lab，未输出凭据。")


if __name__ == "__main__":
    main()
