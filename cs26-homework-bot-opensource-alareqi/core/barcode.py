"""توليد باركود Code 128 للرقم الأكاديمي للطالب مع اسمه ومجموعته على الصورة.
يدعم توليد PNG عبر Pillow (مع خط عربي إن توفّر) أو بمولّد PNG نقي (zlib + struct) بلا أي مكتبة خارجية،
بالإضافة إلى SVG للمعاينة الفورية داخل التطبيق."""
from __future__ import annotations

import io
import os
import struct
import zlib
from html import escape

# أنماط Code 128 القياسية (107 رمز: كل رمز 6 أشرطة/مسافات بمجموع 11 وحدة، ورمز التوقف 13 وحدة)
_CODE128_PATTERNS = [
    "11011001100", "11001101100", "11001100110", "10010011000", "10010001100",
    "10001001100", "10011001000", "10011000100", "10001100100", "11001001000",
    "11001000100", "11000100100", "10110011100", "10011011100", "10011001110",
    "10111001100", "10011101100", "10011100110", "11001110010", "11001011100",
    "11001001110", "11011100100", "11001110100", "11101101110", "11101001100",
    "11100101100", "11100100110", "11101100100", "11100110100", "11100110010",
    "11011011000", "11011000110", "11000110110", "10100011000", "10001011000",
    "10001000110", "10110001000", "10001101000", "10001100010", "11010001000",
    "11000101000", "11000100010", "10110111000", "10110001110", "10001101110",
    "10111011000", "10111000110", "10001110110", "11101110110", "11010001110",
    "11000101110", "11011101000", "11011100010", "11011101110", "11101011000",
    "11101000110", "11100010110", "11101101000", "11101100010", "11100011010",
    "11101111010", "11001000010", "11110001010", "10100110000", "10100001100",
    "10010110000", "10010000110", "10000101100", "10000100110", "10110010000",
    "10110000100", "10011010000", "10011000010", "10000110100", "10000110010",
    "11000010010", "11001010000", "11110111010", "11000010100", "10001111010",
    "10100111100", "10010111100", "10010011110", "10111100100", "10011110100",
    "10011110010", "11110100100", "11110010100", "11110010010", "11011011110",
    "11011110110", "11110110110", "10101111000", "10100011110", "10001011110",
    "10111101000", "10111100010", "11110101000", "11110100010", "10111011110",
    "10111101110", "11101011110", "11110101110", "11010000100", "11010010000",
    "11010011100", "1100011101011",
]
_START_B = 104
_STOP = 106

# خط نقطي مصغّر 5x7 للأرقام والحروف الإنجليزية للـ fallback النقي
_GLYPHS_5X7: dict[str, tuple[int, ...]] = {
    "0": (0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110),
    "1": (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "2": (0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111),
    "3": (0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110),
    "4": (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    "5": (0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110),
    "6": (0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110),
    "7": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000),
    "8": (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    "9": (0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100),
    "C": (0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110),
    "S": (0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110),
    "G": (0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01110),
    "_": (0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b11111),
    "-": (0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000),
    " ": (0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000),
}

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def encode_code128(text: str) -> str:
    """يحوّل نص ASCII (مثل الرقم الأكاديمي) إلى سلسلة بتات Code 128B (1=شريط أسود، 0=مسافة بيضاء)."""
    clean = (text or "").strip()
    if not clean:
        raise ValueError("empty_barcode_text")
    for ch in clean:
        if not (32 <= ord(ch) <= 126):
            raise ValueError("invalid_barcode_char")

    codes = [_START_B]
    checksum = _START_B
    for idx, ch in enumerate(clean, start=1):
        val = ord(ch) - 32
        codes.append(val)
        checksum += val * idx
    codes.append(checksum % 103)
    codes.append(_STOP)
    quiet = "0" * 10
    return quiet + "".join(_CODE128_PATTERNS[c] for c in codes) + quiet


def render_barcode_svg(academic_no: str, full_name: str = "", group: str = "") -> str:
    """يولّد SVG خفيف لمعاينة الباركود داخل التطبيق."""
    bits = encode_code128(academic_no)
    bar_w = 2
    width = len(bits) * bar_w + 32
    height = 152 if full_name else 124
    rects: list[str] = []
    i = 0
    n = len(bits)
    while i < n:
        if bits[i] == "1":
            j = i + 1
            while j < n and bits[j] == "1":
                j += 1
            x = 16 + i * bar_w
            w = (j - i) * bar_w
            rects.append(f'<rect x="{x}" y="16" width="{w}" height="74" fill="#103A2B"/>')
            i = j
        else:
            i += 1
    label = f"{academic_no}" + (f" · {group}" if group else "")
    name_el = (
        f'<text x="{width // 2}" y="138" text-anchor="middle" fill="#566B5F" '
        f'font-family="Segoe UI, Tahoma, sans-serif" font-size="13" font-weight="600">{escape(full_name)}</text>'
        if full_name else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" aria-label="Code 128">'
        f'<rect width="100%" height="100%" rx="12" fill="#FFFFFF"/>'
        + "".join(rects)
        + f'<text x="{width // 2}" y="112" text-anchor="middle" fill="#14271E" '
          f'font-family="monospace" font-size="16" font-weight="700" letter-spacing="2">{escape(label)}</text>'
        + name_el
        + "</svg>"
    )


def _pure_png_barcode(academic_no: str, group: str = "") -> bytes:
    """مولّد PNG نقي (بلا أي مكتبات خارجية) يضمن عمل الباركود في كل بيئة."""
    bits = encode_code128(academic_no)
    scale = 3
    pad_x = 24
    width = len(bits) * scale + pad_x * 2
    height = 180
    # صورة رمادية (8-bit grayscale): 255 أبيض، 0 أسود
    rows = [bytearray([255] * width) for _ in range(height)]

    # رسم أشرطة الباركود
    for idx, b in enumerate(bits):
        if b == "1":
            x0 = pad_x + idx * scale
            for y in range(24, 124):
                for dx in range(scale):
                    rows[y][x0 + dx] = 0

    # كتابة الرقم الأكاديمي والمجموعة أسفل الباركود بخط نقطي مكبّر
    label = f"{academic_no}  {group}".strip()
    px = 2
    char_w = 6 * px
    total_w = len(label) * char_w
    start_x = max(8, (width - total_w) // 2)
    start_y = 136
    for c_idx, ch in enumerate(label.upper()):
        glyph = _GLYPHS_5X7.get(ch, _GLYPHS_5X7[" "])
        cx = start_x + c_idx * char_w
        for gy, mask in enumerate(glyph):
            for gx in range(5):
                if (mask >> (4 - gx)) & 1:
                    for dy in range(px):
                        for dx in range(px):
                            yy = start_y + gy * px + dy
                            xx = cx + gx * px + dx
                            if 0 <= yy < height and 0 <= xx < width:
                                rows[yy][xx] = 20

    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def render_barcode_png(academic_no: str, full_name: str = "", group: str = "") -> bytes:
    """يولّد صورة PNG عالية الوضوح لباركود Code 128 مع اسم الطالب ورقمه الأكاديمي ومجموعته."""
    bits = encode_code128(academic_no)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return _pure_png_barcode(academic_no, group)

    scale = 3
    pad_x = 36
    width = max(460, len(bits) * scale + pad_x * 2)
    height = 240
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # إطار علوي بهوية البوت (أخضر داكن)
    draw.rectangle([0, 0, width, 12], fill=(29, 90, 68))

    bar_w = len(bits) * scale
    start_x = (width - bar_w) // 2
    for idx, b in enumerate(bits):
        if b == "1":
            x0 = start_x + idx * scale
            draw.rectangle([x0, 28, x0 + scale - 1, 138], fill=(16, 58, 43))

    font_path = next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)
    try:
        font_num = ImageFont.truetype(font_path, 22) if font_path else ImageFont.load_default()
        font_name = ImageFont.truetype(font_path, 19) if font_path else ImageFont.load_default()
    except Exception:
        font_num = font_name = ImageFont.load_default()

    num_label = f"{academic_no}" + (f"  ·  {group}" if group else "")
    bbox_num = draw.textbbox((0, 0), num_label, font=font_num)
    w_num = bbox_num[2] - bbox_num[0]
    draw.text(((width - w_num) / 2, 150), num_label, fill=(20, 39, 30), font=font_num)

    if full_name:
        bbox_name = draw.textbbox((0, 0), full_name, font=font_name)
        w_name = bbox_name[2] - bbox_name[0]
        draw.text(((width - w_name) / 2, 188), full_name, fill=(86, 107, 95), font=font_name)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
