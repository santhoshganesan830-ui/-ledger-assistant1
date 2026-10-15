"""
receipt.py
Receipt photo -> {shop, amount, date} using OCR, then the rule engine picks
the category. OCR is never 100% right, so the API only *suggests* values;
the user confirms them in the form before anything is saved.

OCR needs two extras (the rest of the app works without them):
    pip install pytesseract pillow
    + the Tesseract program itself (https://github.com/UB-Mannheim/tesseract/wiki
      on Windows). For Tamil receipts also install the "tam" language data.
"""

import io
import re
from datetime import date, datetime

from categorizer import suggest_category


class OCRUnavailable(RuntimeError):
    pass


def ocr_image(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise OCRUnavailable(
            "OCR libraries missing. Run: pip install pytesseract pillow (and install Tesseract)."
        ) from e
    try:
        img = Image.open(io.BytesIO(data))
        try:
            return pytesseract.image_to_string(img, lang="eng+tam")
        except pytesseract.TesseractError:
            return pytesseract.image_to_string(img, lang="eng")  # Tamil data not installed
    except pytesseract.TesseractNotFoundError as e:
        raise OCRUnavailable("Tesseract program not found. Install it and add it to PATH.") from e


# ---------------------------------------------------------------- parsing --
_NUM = r"(\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_TOTAL_WORDS = r"(?:grand\s*total|net\s*(?:amount|payable|total)|total\s*(?:amount|payable)?|amount\s*(?:due|paid|payable)|bill\s*amount|to\s*pay|மொத்தம்)"
_DATE_PATTERNS = [
    (r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", "ymd"),
    (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", "dmy"),
    (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})\b", "dmy2"),
    (r"\b(\d{1,2})[ -]([A-Za-z]{3,9})[ ,-]*(\d{4})\b", "dMy"),
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def parse_amount(text: str) -> float | None:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 1) a line that says "total"/"amount due" etc. - take the last such line
    for line in reversed(lines):
        if re.search(_TOTAL_WORDS, line, re.I) and not re.search(r"sub\s*total", line, re.I):
            nums = re.findall(_NUM, line.replace("₹", " ").replace("Rs.", " ").replace("Rs", " "))
            if nums:
                return _to_float(nums[-1])
    # 2) fall back to the biggest amount that follows a currency marker
    marked = re.findall(r"(?:₹|rs\.?|inr)\s*" + _NUM, text, re.I)
    if marked:
        return max(_to_float(n) for n in marked)
    return None


def parse_date(text: str) -> str | None:
    for pattern, kind in _DATE_PATTERNS:
        for m in re.finditer(pattern, text):
            try:
                if kind == "ymd":
                    y, mo, d = map(int, m.groups())
                elif kind == "dmy":
                    d, mo, y = map(int, m.groups())
                elif kind == "dmy2":
                    d, mo, y = map(int, m.groups()); y += 2000
                else:
                    d = int(m.group(1)); mo = _MONTHS.get(m.group(2)[:3].lower()); y = int(m.group(3))
                    if not mo:
                        continue
                parsed = date(y, mo, d)
                if parsed <= datetime.now().date():
                    return parsed.isoformat()
            except ValueError:
                continue
    return None


_SKIP_SHOP = re.compile(r"^(tax invoice|invoice|receipt|bill|cash\s*bill|gstin|gst|tel|ph|phone|date|time|\d)", re.I)


def parse_shop(text: str) -> str | None:
    for line in text.splitlines()[:8]:
        line = re.sub(r"[^\w\u0B80-\u0BFF&' .-]", " ", line).strip()
        if len(line) >= 3 and sum(c.isalpha() for c in line) >= 3 and not _SKIP_SHOP.match(line):
            return re.sub(r"\s+", " ", line)[:60]
    return None


def parse_receipt_text(text: str) -> dict:
    amount, shop, when = parse_amount(text), parse_shop(text), parse_date(text)
    # the shop name is the strongest hint; fall back to the whole receipt text
    cat = suggest_category(shop or "", amount)
    if cat["matched"] is None:
        cat = suggest_category(text, amount)
    missing = [n for n, v in (("amount", amount), ("shop", shop), ("date", when)) if v is None]
    review = cat["review"] or bool(missing)
    reason = cat["reason"]
    if missing:
        reason = (reason + " " if reason else "") + f"Could not read: {', '.join(missing)}."
    return {
        "shop": shop, "amount": amount, "date": when,
        "category": cat["category"], "review": review, "reason": reason,
    }
