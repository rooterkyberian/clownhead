import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A directory short enough to hold a unix socket.

    macOS caps an ``AF_UNIX`` path at 104 bytes, which pytest's own temporary directories
    exceed on their own.
    """
    directory = Path(tempfile.mkdtemp(dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)
