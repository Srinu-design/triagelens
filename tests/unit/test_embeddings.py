import asyncio
import json
import math
import os
import subprocess
import sys

import pytest

from app.config import Settings
from app.services.embeddings import FastEmbedder, HashEmbedder, embed_ticket_text, make_embedder


@pytest.mark.parametrize("text", ["", "a", "ab", "Example ticket text", "తెలుగు 🙂"])
async def test_hash_vectors_are_normalized_and_deterministic(text):
    vector = await HashEmbedder().embed(text)
    assert vector == await HashEmbedder().embed(text)
    assert len(vector) == 384
    assert all(isinstance(value, float) for value in vector)
    assert math.hypot(*vector) == pytest.approx(1.0)


async def test_hash_is_stable_across_processes():
    code = (
        "import asyncio, json; from app.services.embeddings import HashEmbedder; "
        "print(json.dumps(asyncio.run(HashEmbedder().embed('Example ticket text'))))"
    )
    output = await asyncio.to_thread(
        subprocess.check_output,
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONHASHSEED": "12345"},
        text=True,
    )
    assert json.loads(output) == await HashEmbedder().embed("Example ticket text")


async def test_embed_many():
    embedder = HashEmbedder()
    assert await embedder.embed_many(["one", "two"]) == [
        await embedder.embed("one"),
        await embedder.embed("two"),
    ]
    assert await embedder.embed_many([]) == []


def test_ticket_text_format():
    assert embed_ticket_text("A title", "A description") == "A title\nA description"
    assert embed_ticket_text("A title", "") == "A title\n"


def test_factory_rechecks_hash_safety():
    settings = Settings.model_construct(app_env="dev", embedding_backend="hash")
    with pytest.raises(ValueError, match="APP_ENV=test"):
        make_embedder(settings)


async def test_fastembed_initialization_is_lazy_and_threaded(monkeypatch):
    import threading

    calls = []

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            calls.append((threading.get_ident(), model_name, kwargs))

        def embed(self, texts):
            assert threading.get_ident() != threading.main_thread().ident
            return [[2.0] * 384 for _ in texts]

    monkeypatch.setattr("app.services.embeddings.TextEmbedding", FakeTextEmbedding)
    settings = Settings.model_construct(model_dir="/cached-model", embed_threads=2)
    embedder = FastEmbedder(settings)
    assert calls == []
    vectors = await asyncio.gather(embedder.embed("one"), embedder.embed("two"))
    assert len(calls) == 1
    assert calls[0][0] != threading.main_thread().ident
    assert calls[0][1:] == (
        "BAAI/bge-small-en-v1.5",
        {"cache_dir": "/cached-model", "threads": 2},
    )
    assert all(math.hypot(*vector) == pytest.approx(1.0) for vector in vectors)


async def test_fastembed_caches_recent_texts(monkeypatch):
    calls = []

    class FakeTextEmbedding:
        def __init__(self, model_name, **kwargs):
            pass

        def embed(self, texts):
            calls.extend(texts)
            return [[float(len(t) + 1)] + [1.0] * 383 for t in texts]

    monkeypatch.setattr("app.services.embeddings.TextEmbedding", FakeTextEmbedding)
    monkeypatch.setattr("app.services.embeddings.CACHE_SIZE", 2)
    embedder = FastEmbedder(Settings.model_construct(model_dir="/m", embed_threads=1))
    first = await embedder.embed("one")
    assert await embedder.embed("one") is first
    await embedder.embed("three")
    await embedder.embed("one")  # refreshes "one", so "three" is the oldest
    await embedder.embed("fifth")
    await embedder.embed("one")
    await embedder.embed("three")
    assert calls == ["one", "three", "fifth", "three"]
