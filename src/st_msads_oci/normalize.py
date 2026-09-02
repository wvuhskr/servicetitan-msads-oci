import hashlib
import re
import unicodedata


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_email(raw):
    # Stripping dots and +aliases looks like a Google rule wrongly applied to a Microsoft feed.
    # It is not. Microsoft strips them too — proven live 2026-08-03, ST job 110000003:
    #   ST email      Jane.Doe.40@yahoo.com   (dotted)
    #   uploaded hash 47606fdfe6cbda2f...        = sha256("janedoe40@yahoo.com")
    #   phone_hash    None                        <- the email hash was the ONLY identifier
    #   MS result     Status = Success
    # Microsoft matched the dot-stripped hash with nothing else to go on. Sending the raw
    # lowercase address instead (sha256 0220d2c1f3f12496...) would have missed.
    # This same value feeds BOTH the CSV Microsoft matches on AND the /map join against
    # GTM-computed hashes, and it is correct for both. Do not "fix" it. See commits
    # 09db553 (the wrong fix) and a5cf73f (the revert, with full evidence).
    if not raw:
        return None
    s = raw.strip().replace(" ", "")
    if "@" not in s:
        return None
    local, domain = s.rsplit("@", 1)
    if "@" in local:
        return None
    local = local.split("+", 1)[0].replace(".", "")
    if not local or not domain:
        return None
    return _strip_accents(f"{local}@{domain}".lower())


def normalize_phone(raw):
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def hash_email(raw):
    n = normalize_email(raw)
    return sha256_hex(n) if n else None


def hash_phone(raw):
    n = normalize_phone(raw)
    return sha256_hex(n) if n else None
