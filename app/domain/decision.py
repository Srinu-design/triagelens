from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    ticket_code: str
    cosine: float
    code_hit: bool


def decide(candidates: list[Candidate], t_auto: float = 0.80, t_review: float = 0.70) -> dict:
    """candidates = same-category masters returned by the hybrid query."""
    if not candidates:
        return {"verdict": "NEW", "master": None, "closest": None, "cosine": None, "reasons": ["no candidates"]}
    code_hits = [c for c in candidates if c.code_hit]
    best = max(code_hits or candidates, key=lambda c: c.cosine)   # an exact code match takes the slot
    reasons = [f"cosine {best.cosine:.3f}"]
    if best.code_hit:
        reasons.insert(0, "same error code")
        # a shared code picks the candidate; it never lowers the auto-merge bar
        verdict = "DUPLICATE" if best.cosine >= t_auto else "REVIEW"
    elif best.cosine >= t_auto:
        verdict = "DUPLICATE"
    elif best.cosine >= t_review:
        verdict = "REVIEW"
    else:
        verdict = "NEW"
    master = None if verdict == "NEW" else best.ticket_code
    return {"verdict": verdict, "master": master, "closest": best.ticket_code,
            "cosine": round(best.cosine, 4), "reasons": reasons}
