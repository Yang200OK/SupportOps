"""截图只验证像素并提取字段，不赋予图片中的指令权限。"""

import base64
import io

import pytest
from PIL import Image, PngImagePlugin

from supportops.rag.screenshots import ScreenshotRequest, prepare_image


def picture(size=(128, 128), fmt="PNG", metadata=None):
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, format=fmt, pnginfo=metadata)
    return base64.b64encode(out.getvalue()).decode()


def test_req1104_actual_png_and_metadata_removed():
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("instruction", "EVAL_INJECTED")
    raw = picture(metadata=metadata)
    image = prepare_image(ScreenshotRequest(image_base64=raw))
    assert image["width"] == 128 and image["height"] == 128
    assert image["image_sha256"] != image["sent_image_sha256"]
    assert b"EVAL_INJECTED" not in base64.b64decode(image["image_base64"])
    original = Image.open(io.BytesIO(base64.b64decode(raw)))
    sent = Image.open(io.BytesIO(base64.b64decode(image["image_base64"])))
    assert original.tobytes() == sent.tobytes()


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/x.png",
        "not-base64",
        "YWJj",
        picture(fmt="JPEG"),
        picture((32, 128)),
        picture((2400, 2400)),
    ],
)
def test_req1104_invalid_or_over_budget_image_is_rejected(raw):
    with pytest.raises(ValueError):
        prepare_image(ScreenshotRequest(image_base64=raw))


def test_req1104_client_cannot_select_model_or_claim_confirmed():
    with pytest.raises(ValueError):
        ScreenshotRequest(image_base64=picture(), model="other", human_reviewed=True)


def test_req1104_transparent_hidden_rgb_is_not_sent_as_visible_pixels():
    out = io.BytesIO()
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(out, format="PNG")
    value = prepare_image(ScreenshotRequest(image_base64=base64.b64encode(out.getvalue()).decode()))
    image = Image.open(io.BytesIO(base64.b64decode(value["image_base64"])))
    assert image.getpixel((0, 0)) == (255, 255, 255)
