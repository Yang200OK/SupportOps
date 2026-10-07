"""保护 Windows PowerShell 5.1 的中文脚本读取方式。"""

import codecs
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", sorted((ROOT / "scripts").glob("*.ps1")))
def test_powershell_script_uses_utf8_bom(path):
    # Windows PowerShell 5.1 没有 BOM 时会用系统 ANSI 编码读取脚本。
    content = path.read_bytes()
    assert content.startswith(codecs.BOM_UTF8), f"脚本缺少 UTF-8 BOM：{path.name}"
    content.decode("utf-8-sig", errors="strict")
