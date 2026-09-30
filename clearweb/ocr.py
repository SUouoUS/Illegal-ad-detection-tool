"""Text in images (optional, default OFF).

Engine: Windows.Media.Ocr (built into Windows 10/11, called through pywinrt).
Why: no model files to ship or download, Korean and Latin text in one recognizer,
~0.02 s per image on CPU, nothing sent outside the PC. Limitation: Windows only,
and the Korean OCR language component must be installed (it is on Korean Windows).

This module only turns image bytes into positioned text and groups it into blocks.
Whether the text looks like an illegal ad is decided by detect.adjudge (reused), per
block, never across images.
"""
from __future__ import annotations

import hashlib
import io
import platform
from dataclasses import dataclass

ENGINE = "Windows.Media.Ocr"
LANG = "ko"

INSTALL_HELP_PACKAGES = (
    "OCR 연결 모듈이 없습니다. 개발 환경에서는 다음을 설치하세요: "
    "pip install pillow winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging "
    "winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections "
    "winrt-Windows.Globalization"
)
INSTALL_HELP_LANGUAGE = (
    "Windows 한국어 OCR 구성 요소가 없습니다. 설정 > 시간 및 언어 > 언어 및 지역 > 한국어 > 언어 옵션에서 "
    "'광학 문자 인식'을 설치하거나, 관리자 PowerShell에서 "
    "Add-WindowsCapability -Online -Name \"Language.OCR~~~ko-KR~0.0.1.0\" 를 실행하세요."
)


@dataclass
class OcrLimits:
    max_images: int = 40            # images per page (top document + frames)
    max_total_images: int = 300     # images per scan
    max_bytes: int = 8_000_000      # downloaded file size
    max_pixels: int = 40_000_000    # refuse to decode larger images (decompression bombs)
    max_side: int = 2400            # larger images are scaled down before OCR
    upscale_below: int = 700        # images whose longer side is smaller are enlarged x2
    min_natural: tuple = (40, 16)   # skip icons / spacers smaller than this
    download_timeout_s: float = 8.0
    total_budget_s: float = 120.0   # all image work of one scan (downloads + OCR)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class OcrUnavailable(Exception):
    pass


_engine = None


def engine_status() -> tuple[bool, str]:
    """(available, message). Never downloads anything."""
    global _engine
    if platform.system() != "Windows":
        return False, "이미지 속 글자 검사는 Windows에서만 지원합니다."
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001
        return False, INSTALL_HELP_PACKAGES
    try:
        if _engine is None:
            _engine = OcrEngine.try_create_from_language(Language(LANG))
    except Exception as e:  # noqa: BLE001
        return False, f"OCR 엔진을 시작하지 못했습니다: {e}"
    if _engine is None:
        return False, INSTALL_HELP_LANGUAGE
    return True, f"{ENGINE} ({_engine.recognizer_language.language_tag}), Windows {platform.version()}"


def engine_info() -> dict:
    ok, msg = engine_status()
    return {"engine": ENGINE, "language": LANG, "available": ok, "detail": msg, "os": platform.platform()}


# ------------------------------------------------------------------ image preparation
@dataclass
class Prepared:
    png: bytes
    width: int
    height: int
    scale: float           # prepared / original
    orig_size: tuple
    thumb_jpeg: bytes
    note: str = ""


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def prepare(data: bytes, limits: OcrLimits) -> Prepared:
    """Decode safely, flatten transparency, scale into the OCR-friendly range, raise contrast."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = limits.max_pixels
    try:
        im = Image.open(io.BytesIO(data))
        w0, h0 = im.size
        if w0 * h0 > limits.max_pixels:
            raise ValueError(f"해상도가 너무 큼({w0}×{h0})")
        im.seek(0)                                   # first frame of GIF / animated WebP
        im = im.convert("RGBA")
    except Image.DecompressionBombError as e:
        raise ValueError(f"해상도가 너무 큼: {e}") from None
    except (OSError, ValueError, SyntaxError) as e:
        raise ValueError(f"이미지를 해석하지 못함({type(e).__name__})") from None
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(bg, im).convert("RGB")

    thumb = rgb.copy()
    thumb.thumbnail((480, 480))
    tb = io.BytesIO()
    thumb.save(tb, "JPEG", quality=82)

    notes = []
    scale = 1.0
    longer = max(w0, h0)
    if longer > limits.max_side:
        scale = limits.max_side / longer
        notes.append(f"{w0}×{h0} → 축소")
    elif longer < limits.upscale_below:
        scale = 2.0
        notes.append("작은 이미지 2배 확대")
    if scale != 1.0:
        rgb = rgb.resize((max(1, round(w0 * scale)), max(1, round(h0 * scale))), Image.LANCZOS)
    gray = ImageOps.autocontrast(rgb.convert("L"), cutoff=1)   # helps light-grey-on-white text
    out = io.BytesIO()
    gray.save(out, "PNG")
    return Prepared(out.getvalue(), gray.width, gray.height, scale, (w0, h0), tb.getvalue(), ", ".join(notes))


# ------------------------------------------------------------------ recognition
async def recognize(png: bytes) -> list[dict]:
    """Lines with word boxes, in prepared-image pixels."""
    ok, msg = engine_status()
    if not ok:
        raise OcrUnavailable(msg)
    from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bmp = await decoder.get_software_bitmap_async()
    if bmp.bitmap_pixel_format not in (BitmapPixelFormat.BGRA8, BitmapPixelFormat.GRAY8):
        bmp = SoftwareBitmap.convert(bmp, BitmapPixelFormat.BGRA8)
    result = await _engine.recognize_async(bmp)
    lines = []
    for line in result.lines:
        words = [{"text": w.text, "x": w.bounding_rect.x, "y": w.bounding_rect.y,
                  "w": w.bounding_rect.width, "h": w.bounding_rect.height} for w in line.words]
        if words:
            lines.append({"text": line.text, "words": words})
    return lines


# ------------------------------------------------------------------ layout: lines -> blocks
def _box(words: list[dict]) -> dict:
    x1 = min(w["x"] for w in words)
    y1 = min(w["y"] for w in words)
    x2 = max(w["x"] + w["w"] for w in words)
    y2 = max(w["y"] + w["h"] for w in words)
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def segments(lines: list[dict]) -> list[dict]:
    """Split a recognised line where words are far apart (two captions on one row)."""
    out = []
    for line in lines:
        cur = [line["words"][0]]
        for w in line["words"][1:]:
            prev = cur[-1]
            gap = w["x"] - (prev["x"] + prev["w"])
            height = max(prev["h"], w["h"], 1)
            if gap > 2.5 * height:
                out.append(cur)
                cur = [w]
            else:
                cur.append(w)
        out.append(cur)
    return [{"text": " ".join(w["text"] for w in seg), **_box(seg)} for seg in out]


def blocks(lines: list[dict]) -> list[dict]:
    """Group line segments into text blocks by proximity: stacked lines that are close
    vertically and overlap horizontally form one block (one caption / one banner line
    group). Far-apart captions stay separate so their words are not combined."""
    segs = sorted(segments(lines), key=lambda s: (s["y"], s["x"]))
    groups: list[list[dict]] = []
    for s in segs:
        placed = False
        for g in groups:
            last = g[-1]
            height = max(last["h"], s["h"], 1)
            vgap = s["y"] - (last["y"] + last["h"])
            overlap = min(last["x"] + last["w"], s["x"] + s["w"]) - max(last["x"], s["x"])
            if -height < vgap <= 1.2 * height and overlap > -0.5 * height:
                g.append(s)
                placed = True
                break
        if not placed:
            groups.append([s])
    out = []
    for g in groups:
        box = {"x": min(s["x"] for s in g), "y": min(s["y"] for s in g)}
        box["w"] = max(s["x"] + s["w"] for s in g) - box["x"]
        box["h"] = max(s["y"] + s["h"] for s in g) - box["y"]
        out.append({"text": " ".join(s["text"] for s in g), "lines": [s["text"] for s in g],
                    **{k: round(v, 1) for k, v in box.items()}})
    return out
