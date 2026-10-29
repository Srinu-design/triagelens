import json

import pytest


@pytest.mark.asyncio
async def test_body_limit_content_length_exceeded(client):
    # 16 KB = 16384 bytes; 16385 bytes should be rejected immediately by Content-Length
    oversized_body = b"x" * 16385
    headers = {"Content-Type": "application/json"}
    response = await client.post("/api/triage/preview", content=oversized_body, headers=headers)
    assert response.status_code == 413
    assert response.headers.get("content-type") == "application/problem+json"
    problem = response.json()
    assert problem["code"] == "PAYLOAD_TOO_LARGE"
    assert problem["status"] == 413
    assert problem["title"] == "Payload Too Large"


@pytest.mark.asyncio
async def test_body_limit_streamed_chunks_exceeded(client):
    # Generator streaming chunks without pre-declared Content-Length
    async def chunk_generator():
        for _ in range(17):
            yield b"a" * 1024  # 17 * 1024 = 17408 bytes > 16384

    headers = {"Content-Type": "application/json"}
    response = await client.post("/api/triage/preview", content=chunk_generator(), headers=headers)
    assert response.status_code == 413
    assert response.headers.get("content-type") == "application/problem+json"
    problem = response.json()
    assert problem["code"] == "PAYLOAD_TOO_LARGE"
    assert problem["status"] == 413


@pytest.mark.asyncio
async def test_body_just_under_limit_passes(client):
    # A JSON body within 16 KB limit passes middleware
    valid_data = {
        "title": "Bug in search",
        "description": "Short description" + " " * 3500,  # <= 4000 char max_length
        "category_override": "BUG_REPORT",
    }
    raw_json = json.dumps(valid_data).encode("utf-8")
    assert len(raw_json) <= 16384

    headers = {"Content-Type": "application/json"}
    response = await client.post("/api/triage/preview", content=raw_json, headers=headers)
    # The body limit middleware passed it; status should be 200
    assert response.status_code == 200
