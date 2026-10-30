import asyncio
import hashlib
import math
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

import anyio
from fastembed import TextEmbedding

from app.config import Settings

DIMENSIONS = 384
MODEL_NAME = "BAAI/bge-small-en-v1.5"
# Recent single-text embeddings (~12 KB each). The demo repeats texts all the time (the default
# search on every page load, presets, a preview then its commit), and the model is ~8 ms of CPU.
CACHE_SIZE = 256


class Embedder(Protocol):
    model_name: str

    async def embed(self, text: str) -> list[float]: ...

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]: ...


def _normalize(values) -> list[float]:
    vector = [float(value) for value in values]
    norm = math.hypot(*vector)
    if len(vector) != DIMENSIONS or not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding must contain 384 finite values with a nonzero norm")
    return [value / norm for value in vector]


class FastEmbedder:
    model_name = MODEL_NAME

    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._load_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(2)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    def _load_model(self):
        return TextEmbedding(
            MODEL_NAME,
            cache_dir=self.settings.model_dir,
            threads=self.settings.embed_threads,
        )

    async def embed(self, text: str) -> list[float]:
        # callers treat vectors as read-only, so a cached list is shared safely
        if (vector := self._cache.get(text)) is not None:
            self._cache.move_to_end(text)
            return vector
        vector = (await self.embed_many([text]))[0]
        self._cache[text] = vector
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return vector

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        async with self._semaphore:
            async with self._load_lock:
                if self._model is None:
                    # DECISION: Lazy model construction keeps loading inside background warm-up.
                    self._model = await anyio.to_thread.run_sync(self._load_model)

            def embed_batch():
                return [_normalize(vector) for vector in self._model.embed(list(texts))]

            return await anyio.to_thread.run_sync(embed_batch)


class HashEmbedder:
    model_name = "hash-trigram-384"

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        # DECISION: Short/empty inputs use one feature so their vectors also have unit norm.
        features = [text] if len(text) < 3 else (text[i : i + 3] for i in range(len(text) - 2))
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            vector[int.from_bytes(digest, "big") % DIMENSIONS] += 1
        return _normalize(vector)

    async def embed_many(self, texts: Sequence[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


def embed_ticket_text(title: str, description: str) -> str:
    return f"{title}\n{description}"


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedding_backend == "hash":
        if settings.app_env != "test":
            raise ValueError("The hash embedding backend is allowed only in APP_ENV=test")
        return HashEmbedder()
    return FastEmbedder(settings)
