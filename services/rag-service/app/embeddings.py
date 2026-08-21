"""Thin wrapper around sentence-transformers, model loaded once per process
(module-level singleton via lru_cache) since encoding individual batches
afterwards is fast.

Loading it is NOT cheap -- correcting an earlier, wrong estimate in this
same docstring ("~100ms-1s on CPU"): a real, direct measurement during this
project's own use showed the torch/sentence-transformers import plus model
instantiation taking 90-150+ seconds under real host resource contention
(and it's not instant even uncontended -- torch's own import alone is a
real cost). `app/main.py`'s startup lifespan calls `warm_up()` below
specifically so this cost is paid once, at container boot (with a
healthcheck `start_period` giving it room), instead of being silently
deferred onto whichever real user's request happens to be first -- letting
that happen was a genuine, observed bug: a live `/chat` request timing out
because rag-service's first-ever query had to pay this entire cost inline.
"""
from functools import lru_cache
from typing import List

from app.config import EMBEDDING_MODEL_NAME


@lru_cache(maxsize=1)
def _get_model():
    # Imported lazily so `python -c "import app.config"` etc. stay fast and so
    # test collection doesn't pay the torch/sentence-transformers import cost
    # unless embeddings are actually needed.
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")


def warm_up() -> None:
    """Force the model to load NOW, synchronously -- call once, at startup.
    A no-op on every call after the first, since `_get_model()` is cached."""
    _get_model()


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts. Returns one vector per input text."""
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


def embed_query(query: str) -> List[float]:
    return embed_texts([query])[0]
