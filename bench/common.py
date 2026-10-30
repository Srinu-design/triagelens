import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

PLATFORMS = ("Android", "iOS", "web", "desktop", "Safari", "Chrome")
AREAS = (
    "checkout",
    "login",
    "export",
    "search",
    "notifications",
    "invoices",
    "dashboard",
    "profile photo",
    "file upload",
    "settings",
)
SYMPTOMS = (
    "crashes",
    "freezes",
    "times out",
    "shows wrong totals",
    "logs me out",
    "duplicates entries",
    "renders blank",
    "rejects valid input",
)
CONTEXTS = (
    "after the update",
    "on slow networks",
    "in dark mode",
    "for large files",
    "at month end",
)
CATEGORIES = ("BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY")
ERROR_CODES = tuple(
    f"ERR_{area.upper().replace(' ', '_')}_{n}" for area in AREAS for n in range(50)
)
INDEXES = ("bench_hnsw_f32", "bench_hnsw_f16", "bench_hnsw_f16_enquiries")
ROOT = Path(__file__).resolve().parents[1]
BUILD_PATH = ROOT / "bench" / "build.json"
RESULTS_PATH = ROOT / "bench" / "RESULTS.md"


@dataclass(frozen=True)
class Ticket:
    id: int
    title: str
    description: str
    category: str
    error_code: str | None


def build_tickets(rows: int, seed: int, *, queries: bool = False) -> list[Ticket]:
    if rows < 1:
        raise ValueError("rows must be positive")
    rng = random.Random(seed)
    tickets = []
    for item_id in range(1, rows + 1):
        category = rng.choices(CATEGORIES, weights=(70, 20, 10), k=1)[0]
        platform, area = rng.choice(PLATFORMS), rng.choice(AREAS)
        symptom, context = rng.choice(SYMPTOMS), rng.choice(CONTEXTS)
        # DECISION: Category-specific wording combines all four specified template dimensions.
        if category == "BUG_REPORT":
            title = f"{platform} {area} {symptom}"
            description = f"The {area} on {platform} {symptom} {context}."
        elif category == "FEATURE_REQUEST":
            title = f"Add {area} support on {platform}"
            description = f"Please improve {area} on {platform} {context}; it currently {symptom}."
        else:
            title = f"How does {area} work on {platform}?"
            description = f"How can I use {area} on {platform} {context} when it {symptom}?"
        code = None
        if (queries or category == "BUG_REPORT") and rng.random() < 0.1:
            code = f"ERR_{area.upper().replace(' ', '_')}_{rng.randrange(50)}"
        tickets.append(Ticket(item_id, title, description, category, code))
    return tickets


def recall_at_10(actual_distances, d10: float) -> float:
    return sum(distance <= d10 + 1e-6 for distance in actual_distances[:10]) / 10


def percentile(values, p: float) -> float:
    if not values or not 0 <= p <= 100:
        raise ValueError("percentile needs values and a percentile between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def database_url(key: str) -> str:
    expected = {
        "BENCH_DATABASE_URL": "triagelens_bench",
        "BENCH_APP_DATABASE_URL": "triagelens_bench_app",
    }[key]
    value = os.environ.get(key, "")
    # DECISION: Destructive benchmark setup is restricted to the two named bench databases.
    if urlsplit(value).path != f"/{expected}":
        raise ValueError(f"{key} must target {expected}")
    return value
