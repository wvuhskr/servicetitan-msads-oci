"""Deterministic US-phone and Latin-to-ASCII email contract shared with the Worker.

Unsupported email scripts return None rather than relying on runtime-specific
Unicode tables. See docs/normalization.md for supported ranges and migration.
"""
import hashlib
import re
import unicodedata

# Fixed ranges, deliberately independent of each runtime's Unicode category tables.
_SPACE = "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200b))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"
_MARKS = r"[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]"
_SUPPORTED = re.compile(r"[\x20-\x7e\u00c0-\u024f\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u1e00-\u1eff\u20d0-\u20ff\ufe20-\ufe2f\uff01-\uff5e]*")


def normalize_email(raw):
    if not isinstance(raw, str):
        return None
    s = raw.strip(_SPACE).replace(" ", "")
    if not _SUPPORTED.fullmatch(s):
        return None
    s = re.sub(_MARKS, "", unicodedata.normalize("NFKD", s)).lower()
    if not s.isascii() or re.search(r"[\x00-\x20\x7f]", s) or s.count("@") != 1:
        return None
    local, domain = s.split("@")
    local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}" if local and domain else None


def ascii_digits(raw):
    if not isinstance(raw, str):
        return ""
    return re.sub(r"[^0-9]", "", raw)


def normalize_phone(raw):
    digits = ascii_digits(raw)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def last10(raw):
    digits = ascii_digits(raw)
    return digits[-10:] if len(digits) >= 10 else None


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_email(raw):
    n = normalize_email(raw)
    return sha256_hex(n) if n else None


def hash_phone(raw):
    n = normalize_phone(raw)
    return sha256_hex(n) if n else None
