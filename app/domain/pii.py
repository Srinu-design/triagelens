import re

_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],
      [4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],
      [8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
_INV = [0,4,3,2,1,5,6,7,8,9]


def verhoeff_valid(num: str) -> bool:          # Aadhaar check digit
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(num: str) -> str:     # used by tests to build a valid sample
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def luhn_valid(num: str) -> bool:              # payment cards
    total, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


RULES = [   # (label, pattern, validator) — ORDER MATTERS
    ("SECRET",  re.compile(r"(?i)\bbearer\s+[a-z0-9\-._~+/]{8,}=*"), None),
    ("SECRET",  re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"), None),
    ("SECRET",  re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    ("SECRET",  re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b"), None),
    ("SECRET",  re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), None),
    ("SECRET",  re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|pwd|token)\s*[:=]\s*\S+"), None),
    ("EMAIL",   re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), None),
    ("UPI",     re.compile(r"\b[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}\b"), None),
    ("CARD",    re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])"), "luhn"),
    ("AADHAAR", re.compile(r"(?<![\d-])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\d-])"), "verhoeff"),
    ("PAN",     re.compile(r"\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b"), None),
    ("PHONE",   re.compile(r"(?<![\w+])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\w)"), None),
    ("PHONE",   re.compile(r"(?<![\w+])\+(?:[1-9]\d{0,2})(?:[\s-]?\d){7,14}(?!\w)"), None),
    ("IP",      re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
                           r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])"), None),
]


def scrub(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for label, pattern, validator in RULES:
        def repl(m, label=label, validator=validator):
            raw = m.group(0)
            if validator:
                digits = re.sub(r"\D", "", raw)
                ok = luhn_valid(digits) if validator == "luhn" else verhoeff_valid(digits)
                if not ok:
                    return raw            # checksum fails: not PII, leave it
            counts[label] = counts.get(label, 0) + 1
            return f"[REDACTED_{label}]"
        text = pattern.sub(repl, text)
    return text, counts
