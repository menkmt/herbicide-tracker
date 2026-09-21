"""Keeping a document's text without keeping the document.

Original source files are large — a scanned county permit is six megabytes of
page images — and expensive to read: OCR'ing one takes about a minute. The
text inside it is a few tens of kilobytes and is what every later operation
actually wants.

So the text is extracted once, at import, and stored in the database. After
that the tracker can search a document, quote the sentence a published fact
came from, or re-parse it with an improved parser, all without touching the
original or running OCR again.

Where the original came from Inquisitor, it stays in Inquisitor. That system
is an evidence vault: it keeps immutable originals with SHA-256 hashes and a
documented chain of custody, which is precisely the job. Copying the bytes
into a second store would create a second system of record for the same
document, double the storage bill, and leave two answers to the question of
what the original was.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Above this, the text is truncated before storage. Reached only by very long
#: documents; the truncation is recorded so a reader knows it happened.
MAX_STORED_TEXT_BYTES = 2_000_000


class StorageMode:
    LOCAL = "local"
    INQUISITOR = "inquisitor"
    NONE = "none"


@dataclass
class StoredText:
    text: str
    byte_size: int
    truncated: bool = False

    @property
    def note(self) -> str | None:
        if not self.truncated:
            return None
        return (
            f"the extracted text was truncated at {MAX_STORED_TEXT_BYTES:,} bytes; "
            "the original document holds the remainder"
        )


def prepare_text(text: str | None) -> StoredText | None:
    """Prepare a document's text for storage."""
    if not text or not text.strip():
        return None
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_STORED_TEXT_BYTES:
        return StoredText(text=text, byte_size=len(encoded))
    clipped = encoded[:MAX_STORED_TEXT_BYTES].decode("utf-8", "ignore")
    return StoredText(text=clipped, byte_size=len(clipped.encode("utf-8")), truncated=True)


def decide_storage(origin: str, *, inquisitor_url: str | None = None) -> str:
    """Where the original bytes should live for a file from this origin."""
    if origin == "cpra" and inquisitor_url:
        return StorageMode.INQUISITOR
    return StorageMode.LOCAL


def retrieval_note(mode: str, *, agency: str | None = None, request: str | None = None) -> str:
    """What a page says about where the original can be obtained."""
    if mode == StorageMode.INQUISITOR:
        parts = ["The original document is held in the evidence vault"]
        if agency:
            parts.append(f"as produced by {agency}")
        if request:
            parts.append(f"under records request {request}")
        return " ".join(parts) + "."
    if mode == StorageMode.LOCAL:
        return "The original document is held by the tracker and is available on request."
    return "The original document is not currently held; only its extracted text remains."
