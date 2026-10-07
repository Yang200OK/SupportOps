"""请求边界拒绝超限、伪造与不安全文件名。"""

import base64

import pytest
from pydantic import ValidationError

from supportops.documents.contracts import ImportRequest


def sample(**changes):
    return {
        "source_key": "configuration",
        "title": "配置资料",
        "product": "relaydesk",
        "product_version": "1.0",
        "source_type": "demo_product",
        "license": "CC0-1.0",
        "filename": "配置.md",
        "format": "md",
        "content_base64": base64.b64encode(b"# Configuration").decode(),
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"filename": "C:\\a.md"},
        {"filename": "a\r\n.md"},
        {"filename": "a.pdf"},
        {"source_key": "../doc"},
        {"title": " "},
        {"title": "中" * 201},
        {"product_version": 1.0},
        {"importer_id": "forged"},
        {"status": "parsed"},
    ],
)
def test_req302_bad_metadata_is_rejected(changes):
    with pytest.raises(ValidationError):
        ImportRequest.model_validate(sample(**changes))


def test_req302_byte_limit_is_on_decoded_file():
    with pytest.raises(ValidationError):
        ImportRequest.model_validate(
            sample(content_base64=base64.b64encode(b"x" * 2097153).decode())
        )
    assert ImportRequest.model_validate(sample()).raw_bytes() == b"# Configuration"
