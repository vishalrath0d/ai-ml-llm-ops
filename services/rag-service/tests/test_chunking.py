from app.chunking import chunk_text
from app.config import CHUNK_SIZE_CHARS


def test_chunk_text_empty_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_chunk_text_short_text_is_a_single_chunk():
    text = "This is a short piece of text about Onwly."
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_long_text_produces_multiple_bounded_chunks():
    paragraphs = [
        f"Paragraph {i}. " + ("Filler sentence about Onwly features. " * 40)
        for i in range(8)
    ]
    long_text = "\n\n".join(paragraphs)

    chunks = chunk_text(long_text)

    assert len(chunks) > 1
    # RecursiveCharacterTextSplitter tries to respect chunk_size, with some
    # slack allowed at separator boundaries.
    assert all(len(c) <= CHUNK_SIZE_CHARS + 100 for c in chunks)
    # Reassembling should preserve (most of) the original content start/end.
    assert long_text.strip().startswith(chunks[0][:20])


def test_chunk_text_overlap_is_configured_and_nonzero():
    from app.config import CHUNK_OVERLAP_CHARS

    assert 0 < CHUNK_OVERLAP_CHARS < CHUNK_SIZE_CHARS

    # Distinct, non-repeating sentences so we can check for verbatim overlap
    # between consecutive chunks without relying on incidental repetition.
    sentences = [f"This is unique sentence number {i} about Onwly. " for i in range(300)]
    long_text = "".join(sentences)

    chunks = chunk_text(long_text)
    assert len(chunks) >= 2

    tail_of_first = chunks[0][-30:]
    assert tail_of_first in chunks[1]
