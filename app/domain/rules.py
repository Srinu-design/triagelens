BUG_CUES = ["crash", "crashes", "error", "fails", "failed", "freeze", "freezes", "frozen", "hang", "hangs",
            "broken", "not working", "doesn't work", "does not", "never", "timeout", "times out", "wrong",
            "missing", "blank", "empty", "twice", "rejected", "stuck", "logged out", "drops", "closes",
            "locks up", "stops responding", "keeps", "still", "bug", "500", "504", "413", "cannot", "can't"]
FEATURE_CUES = ["add", "support", "would like", "would love", "would be nice", "please", "feature", "allow",
                "option", "want", "wish", "let ", "need", "request", "offer", "mode for", "integration with",
                "sync with", "view for"]
ENQUIRY_STARTS = ("how ", "what ", "where ", "when ", "can ", "can we", "do you", "is there", "is our",
                  "which ", "who ", "why ")
ENQUIRY_CUES = ["pricing", "price", "invoice", "billing cycle", "plan", "refund", "discount", "support hours",
                "data stored", "hosted", "licence", "license", "seats", "on-premise", "api"]
SUBSYSTEMS = [  # first match wins
    ("Payments", ["checkout", "payment", "pay ", "paying", "upi", "refund", "gateway"]),
    ("Auth", ["login", "log in", "logged out", "password", "2fa", "two-factor", "authenticator", "session", "otp"]),
    ("Notifications", ["notification", "push", "alert"]),
    ("Billing", ["invoice", "gst", "billing", "plan", "seats", "licence", "subscription"]),
    ("Reports", ["report", "export", "csv", "excel", "xlsx"]),
    ("Search", ["search"]),
    ("Analytics", ["dashboard", "chart", "analytics", "graph"]),
    ("Imports", ["import", "upload", "bulk"]),
    ("Profile", ["profile", "avatar", "photo", "picture", "cropper"]),
    ("Localization", ["language", "hindi", "tamil", "translation"]),
    ("Integrations", ["slack", "api", "webhook", "crm", "outlook", "calendar"]),
]


def classify(title: str, description: str) -> dict:
    t = f"{title} {description}".lower()
    head = title.lower().strip()
    bug = sum(c in t for c in BUG_CUES)
    feat = sum(c in t for c in FEATURE_CUES)
    enq = (sum(c in t for c in ENQUIRY_CUES) + (2 if head.startswith(ENQUIRY_STARTS) else 0)
           + (1 if head.endswith("?") else 0))
    scores = {"BUG_REPORT": bug, "FEATURE_REQUEST": feat, "CLIENT_ENQUIRY": enq}
    category = max(scores, key=lambda k: (scores[k], k == "BUG_REPORT"))      # ties go to bug
    if category == "BUG_REPORT":
        if any(k in t for k in ["checkout", "payment", "data loss", "security", "504"]):
            severity = "CRITICAL"
        elif any(k in t for k in ["crash", "closes", "cannot", "can't", "never", "freezes", "locks up"]):
            severity = "HIGH"
        elif any(k in t for k in ["typo", "alignment", "cosmetic", "twice"]):
            severity = "LOW"
        else:
            severity = "MEDIUM"
    else:
        severity = "LOW" if category == "CLIENT_ENQUIRY" else "MEDIUM"
    subsystem = next((name for name, keys in SUBSYSTEMS if any(k in t for k in keys)), "General")
    return {"category": category, "severity": severity, "subsystem": subsystem,
            "sentiment": "NEUTRAL", "summary": title[:120]}
