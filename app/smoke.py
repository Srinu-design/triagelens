"""Deploy smoke test: python -m app.smoke [base_url]

Runs read-only smoke checks against a live TriageLens container:
1. /readyz returns 200 and reports model_ready: True
2. GET /api/search?q=Mobile avatar upload screen hangs returns BUG-102 as fused #1
3. POST /api/triage/preview with demo step 5 returns DUPLICATE of FEAT-201
4. GET / includes the Content-Security-Policy header

Exits 0 on success, or non-zero with a single diagnostic line on failure.
Does not write or commit any data.
"""

import json
import sys
import urllib.parse
import urllib.request


def _request(base: str, path: str, payload: dict | None = None) -> tuple[int, dict, dict]:
    """GET path, or POST payload as JSON; return status, lower-cased headers and the JSON body."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        raw = resp.read()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}  # GET / is HTML
        return resp.status, headers, body


def smoke(base_url: str) -> None:
    base = base_url.rstrip("/")

    # 1. /readyz is 200 with model_ready true
    status, _, ready = _request(base, "/readyz")
    if status != 200:
        raise RuntimeError(f"/readyz returned status {status}")
    if ready.get("model_ready") is not True:
        raise RuntimeError(f"/readyz model_ready is not True: got {ready.get('model_ready')}")

    # 2. GET /api/search?q=Mobile avatar upload screen hangs has BUG-102 as fused #1
    query = urllib.parse.quote("Mobile avatar upload screen hangs")
    status, _, search_data = _request(base, f"/api/search?q={query}")
    if status != 200:
        raise RuntimeError(f"/api/search returned status {status}")
    fused = search_data.get("fused", [])
    if not fused:
        raise RuntimeError("/api/search returned empty fused list")
    first_code = fused[0].get("ticket_code")
    if first_code != "BUG-102":
        raise RuntimeError(f"/api/search fused #1 expected BUG-102, got {first_code}")

    # 3. POST /api/triage/preview with demo step 5 is DUPLICATE of FEAT-201
    preview_payload = {
        "title": "Night mode for checkout on Android",
        "description": "Would love a dark theme when paying in the Android app.",
        "category_override": "FEATURE_REQUEST",
    }
    status, _, preview_data = _request(base, "/api/triage/preview", preview_payload)
    if status != 200:
        raise RuntimeError(f"/api/triage/preview returned status {status}")
    decision = preview_data.get("decision", {})
    verdict = decision.get("verdict")
    master = decision.get("master")
    if verdict != "DUPLICATE" or master != "FEAT-201":
        raise RuntimeError(
            f"/api/triage/preview expected DUPLICATE of FEAT-201, got {verdict=} {master=}"
        )

    # 4. Response to / has Content-Security-Policy header
    status, headers, _ = _request(base, "/")
    if status != 200:
        raise RuntimeError(f"/ returned status {status}")
    if "content-security-policy" not in headers:
        raise RuntimeError("Response to / is missing Content-Security-Policy header")


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    try:
        smoke(base_url)
    except Exception as exc:
        print(f"smoke FAIL: {exc}")
        return 1
    print("smoke pass: readyz, hybrid search BUG-102, triage preview DUPLICATE FEAT-201, CSP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
