"""Recursive, sentence-aware chunking.

Uses langchain-text-splitters' RecursiveCharacterTextSplitter, which tries a
sequence of separators (paragraph -> line -> sentence -> word -> char) so
chunks break on natural boundaries wherever possible instead of mid-sentence.
"""
from typing import List

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_CHARS,
    chunk_overlap=CHUNK_OVERLAP_CHARS,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def chunk_text(text: str) -> List[str]:
    """Split `text` into overlapping chunks (~500 tokens each by default)."""
    text = text.strip()
    if not text:
        return []
    return _splitter.split_text(text)
