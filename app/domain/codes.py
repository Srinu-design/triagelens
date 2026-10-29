import re

CODE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", re.IGNORECASE)


def extract_codes(text: str) -> list[str]:
    codes = {m.group(0).upper() for m in CODE_RE.finditer(text)}
    return sorted(c for c in codes if not c.startswith("REDACTED_"))[:5]
