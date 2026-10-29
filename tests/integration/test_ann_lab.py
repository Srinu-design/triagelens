import pytest


async def test_iterative_scan_repairs_filtered_ann(client):
    params = {
        "q": "checkout crashes in dark mode",
        "category": "FEATURE_REQUEST",
        "force": 1,
        "ef_search": 5,
    }
    off = await client.get("/api/ann-lab", params={**params, "iterative": "off"})
    strict = await client.get("/api/ann-lab", params={**params, "iterative": "strict_order"})
    assert off.status_code == strict.status_code == 200
    assert off.json()["rows_returned"] < 5
    assert strict.json()["rows_returned"] == 5
    for response in (off, strict):
        data = response.json()
        assert data["index_scan"] is True
        assert "Index Scan" in data["plan_nodes"]
        assert len(data["codes"]) == data["rows_returned"]
        assert all(code.startswith("FEAT-") for code in data["codes"])
        assert data["settings"] == {
            "force": 1,
            "ef_search": 5,
            "iterative": "off" if response is off else "strict_order",
        }


@pytest.mark.parametrize(
    "setting, value",
    [
        ("ef_search", "6"),
        ("ef_search", "0"),
        ("ef_search", "5; DROP TABLE sessions"),
        ("iterative", "strict"),
        ("iterative", "off; SELECT 1"),
        ("force", "2"),
        ("force", "-1"),
        ("force", "true"),
        ("category", "FEATURE_REQUEST OR true"),
        ("q", ""),
        ("q", "x" * 501),
    ],
)
async def test_ann_rejects_invalid_values(client, setting, value):
    response = await client.get("/api/ann-lab", params={"q": "checkout", setting: value})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("effort", [5, 10, 40])
@pytest.mark.parametrize("iterative", ["off", "strict_order", "relaxed_order"])
async def test_ann_accepts_all_whitelisted_values(client, effort, iterative):
    response = await client.get(
        "/api/ann-lab",
        params={"q": "checkout", "ef_search": effort, "iterative": iterative, "force": 0},
    )
    assert response.status_code == 200
    assert response.json()["settings"] == {"force": 0, "ef_search": effort, "iterative": iterative}
    body = response.json()
    # an autoanalyze mid-run can tip the planner onto HNSW, where ef_search=5 may return fewer
    if not body["index_scan"]:
        assert body["rows_returned"] == 5


async def test_ann_scrubs_before_embedding(client, application, embedder):
    seen = []

    class RecordingEmbedder:
        async def embed(self, value):
            seen.append(value)
            return await embedder.embed(value)

    application.state.embedder = RecordingEmbedder()
    response = await client.get(
        "/api/ann-lab", params={"q": "checkout alice@example.com", "force": 0}
    )
    assert response.status_code == 200
    assert seen == ["checkout [REDACTED_EMAIL]"]
