from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Protocol


DEFAULT_CHINESE_MODEL = "BAAI/bge-small-zh-v1.5"
HASH_MODEL = "hash-local-v1"
HASH_DIM = 256


class EmbeddingBackend(Protocol):
    model: str
    dim: int

    def encode(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass
class BackendStatus:
    model: str
    dim: int
    provider: str
    fallback_reason: str | None = None


def normalize_vector(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        return values
    return [round(value / norm, 6) for value in values]


class HashEmbeddingBackend:
    model = HASH_MODEL
    dim = HASH_DIM

    def encode(self, texts: list[str]) -> list[list[float]]:
        from kb.indexing import text_vector

        return [text_vector(text, self.dim) for text in texts]


class SentenceTransformerEmbeddingBackend:
    def __init__(self, model: str = DEFAULT_CHINESE_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self.model = model
        self._encoder = SentenceTransformer(model)
        dim = getattr(self._encoder, "get_sentence_embedding_dimension", lambda: None)()
        self.dim = int(dim or 0)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._encoder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        out: list[list[float]] = []
        for vector in vectors:
            values = [float(value) for value in vector]
            out.append([round(value, 6) for value in values])
        if not self.dim and out:
            self.dim = len(out[0])
        return out


def resolve_embedding_backend(preferred_model: str | None = None) -> tuple[EmbeddingBackend, BackendStatus]:
    provider = os.environ.get("KB_EMBEDDING_PROVIDER", "sentence-transformers").strip().lower()
    model = preferred_model or os.environ.get("KB_EMBEDDING_MODEL") or DEFAULT_CHINESE_MODEL
    if provider in {"hash", "hash-local", HASH_MODEL} or model == HASH_MODEL:
        backend = HashEmbeddingBackend()
        return backend, BackendStatus(model=backend.model, dim=backend.dim, provider="hash")
    try:
        backend = SentenceTransformerEmbeddingBackend(model)
        return backend, BackendStatus(model=backend.model, dim=backend.dim, provider="sentence-transformers")
    except Exception as exc:
        backend = HashEmbeddingBackend()
        return backend, BackendStatus(
            model=backend.model,
            dim=backend.dim,
            provider="hash",
            fallback_reason=f"{type(exc).__name__}: {exc}",
        )
