"""Reading transcripts for the references named in them, over the bytes they were written in.

One filter keystroke can send a search across the whole corpus, which runs to hundreds of
megabytes, so what the engine does with a pattern decides whether the answer arrives at
all. CPython scans a literal by jumping between its occurrences, and gives that up for
either of the two things these patterns need: ``re.IGNORECASE`` denies it the jump, and so
does a lookbehind. Measured over 148 MB of real transcripts, the pattern this module
replaces ran at 117 MB/s where RE2 answers the same question at 3163 MB/s.

RE2 has no lookaround of any kind, so the boundaries live outside the pattern here, checked
against the bytes on either side of a match. Every engine fast enough to be worth the swap
charges that same price: Rust's regex crate and hyperscan both refuse ``(?<!`` too, for the
reason RE2 does, which is that a linear-time engine cannot backtrack to satisfy one.

Transcripts are mapped rather than read in chunks. A match then carries its own absolute
offset instead of one carried alongside a window, and a mention that would have straddled
the boundary between two chunks has no boundary left to straddle.
"""

from __future__ import annotations

import mmap
import string
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import re2

WORD_BYTES = (string.ascii_letters + string.digits + "_").encode()
DIGIT_BYTES = string.digits.encode()

NAME_BYTES = WORD_BYTES + b".-"
"""What may not precede a repository name, because ``data-platform`` is one of ``my-data-platform``."""

KEY_BYTES = WORD_BYTES + b"-"
"""The same for a Jira project key, which is spelled without dots."""

type Scanned = bytes | mmap.mmap
"""Whatever a scan runs over: a mapped transcript, or bytes a test handed straight in."""


@dataclass(frozen=True)
class Mention:
    """A compiled test for whether a transcript names one reference and not a longer one.

    The pattern matches the reference alone. What keeps ``widgets#309`` out of an answer
    about ``widgets#3090``, and ``my-data-platform#309`` out of one about
    ``data-platform#309``, is the pair of checks around it: nothing from ``preceded_by``
    immediately to the left, and no digit immediately to the right.

    Both are cheap because there is so little to check. A full corpus scan for one
    reference offers a few hundred candidate offsets at most, so the per-match work never
    approaches the cost of the scan that found them.
    """

    pattern: Any
    preceded_by: bytes

    def found_in(self, data: Scanned) -> bool:
        """Whether the reference is named anywhere in ``data``."""
        size = len(data)
        for match in self.pattern.finditer(data):
            start, end = match.start(), match.end()
            if start and data[start - 1] in self.preceded_by:
                continue
            if end < size and data[end] in DIGIT_BYTES:
                continue
            return True
        return False


def mention(expression: str, preceded_by: bytes) -> Mention:
    """Compile a case-insensitive mention test from the pattern that spells one reference.

    Case-insensitive because a reference is written down by whoever pasted it, and a
    repository named in prose arrives in whatever case they typed. RE2 folds ASCII case
    inside the automaton it builds, so the flag costs a fraction of what it costs CPython,
    which has to give up scanning by literal to honour it.
    """
    return Mention(re2.compile(f"(?i){expression}".encode()), preceded_by)


def mapped(path: Path) -> Iterator[mmap.mmap]:
    """The transcript mapped into memory, yielded once, or nothing where it cannot be read.

    Mapped as bytes rather than parsed as the JSON it is: the questions asked of a
    transcript are only ever whether some string appears in it and where, and decoding
    megabytes of tool output to answer that costs far more than the answer is worth.

    Yielding rather than returning is what lets a caller write the whole read as one
    expression over the file's bytes, and what keeps the mapping open for exactly as long
    as that expression needs it.

    A transcript that cannot be read yields nothing, which every caller reads as naming
    nothing — the same answer as an empty file, and the right one for a question about what
    a session said. An empty file is the same case for a different reason: there is nothing
    to map, and :func:`mmap.mmap` says so by raising.

    Only the mapping is guarded. An error raised by the caller while it holds the buffer
    travels back out untouched, rather than being mistaken here for a file that went away.
    """
    try:
        handle = path.open("rb")
    except OSError:
        return
    with handle:
        try:
            buffer = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError):
            return
        with buffer:
            yield buffer
