"""Enough of RFC 6455 to hold a JSON-RPC conversation over a Unix socket.

The Codex app-server listens on a Unix socket that speaks WebSocket rather than raw bytes:
newline-delimited JSON onto it is closed without a reply, and an ``Upgrade: websocket``
request is answered ``101 Switching Protocols``. So reaching it means framing.

What is here is the client half of a text-only conversation: masked text frames out,
frames of any length back, with continuation reassembled and pings answered. Binary
frames, extensions and compression are all things the app-server never sends, and a
frame this module cannot read is an error rather than a guess.

The socket is supplied rather than opened, because who dials is the caller's business and
a connection that fails to open has a better error to give than this module could.
"""

from __future__ import annotations

import base64
import os
import socket
import struct
from collections.abc import Iterator

HANDSHAKE_TIMEOUT = 5.0
MAX_FRAME_BYTES = 64 * 1024 * 1024
CONTINUATION = 0x0
TEXT = 0x1
BINARY = 0x2
CLOSE = 0x8
PING = 0x9
PONG = 0xA
FINAL_BIT = 0x80
MASK_BIT = 0x80
OPCODE_MASK = 0x0F
LENGTH_MASK = 0x7F
SHORT_LENGTH = 126
LONG_LENGTH = 127


class ProtocolError(RuntimeError):
    """The peer said something this client cannot read."""


class Connection:
    """A WebSocket conversation over a socket somebody else opened.

    Used as a context manager so the close frame is sent even when a request raises.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock
        self._buffer = b""

    def __enter__(self) -> Connection:
        """Enter the conversation, which is already open by the time anyone has one."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the conversation however the block ended."""
        self.close()

    def handshake(self, host: str = "localhost", path: str = "/") -> None:
        """Perform the opening HTTP upgrade, raising :class:`ProtocolError` if refused."""
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._socket.sendall(request.encode())
        response = self._read_until(b"\r\n\r\n")
        status = response.split(b"\r\n", 1)[0].decode(errors="replace")
        if "101" not in status:
            raise ProtocolError(f"websocket upgrade refused: {status or 'no response'}")

    def send(self, text: str) -> None:
        """Send one text frame, masked as the specification requires of a client."""
        self._socket.sendall(_text_frame(text))

    def messages(self) -> Iterator[str]:
        """Yield text messages as they arrive, until the peer closes or the socket times out.

        Pings are answered where they appear, so a caller that stops reading early stops
        answering them too. That is the right trade for a request-and-reply client: the
        connection is short-lived, and a pong owed on a socket about to close is owed to
        nobody.
        """
        pending: list[bytes] = []
        while True:
            try:
                frame = self._read_frame()
            except (TimeoutError, ProtocolError, OSError):
                return
            if frame is None:
                return
            opcode, payload, final = frame
            if opcode == PING:
                self._socket.sendall(_frame(PONG, payload))
                continue
            if opcode in (PONG, BINARY):
                continue
            if opcode == CLOSE:
                return
            pending.append(payload)
            if final:
                yield b"".join(pending).decode(errors="replace")
                pending = []

    def close(self) -> None:
        """Send a close frame if the socket still takes one, then drop it either way."""
        try:
            self._socket.sendall(_frame(CLOSE, b""))
        except OSError:
            pass
        finally:
            self._socket.close()

    def _read_frame(self) -> tuple[int, bytes, bool] | None:
        header = self._read_exactly(2)
        if header is None:
            return None
        final = bool(header[0] & FINAL_BIT)
        opcode = header[0] & OPCODE_MASK
        masked = bool(header[1] & MASK_BIT)
        length = header[1] & LENGTH_MASK
        if length == SHORT_LENGTH:
            extended = self._read_exactly(2)
            if extended is None:
                return None
            length = struct.unpack("!H", extended)[0]
        elif length == LONG_LENGTH:
            extended = self._read_exactly(8)
            if extended is None:
                return None
            length = struct.unpack("!Q", extended)[0]
        if length > MAX_FRAME_BYTES:
            raise ProtocolError(f"frame of {length} bytes is larger than this client will read")
        mask = self._read_exactly(4) if masked else None
        payload = self._read_exactly(length) if length else b""
        if payload is None:
            return None
        if mask is not None:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return (TEXT if opcode == CONTINUATION else opcode), payload, final

    def _read_exactly(self, count: int) -> bytes | None:
        while len(self._buffer) < count:
            chunk = self._socket.recv(65536)
            if not chunk:
                return None
            self._buffer += chunk
        taken, self._buffer = self._buffer[:count], self._buffer[count:]
        return taken

    def _read_until(self, terminator: bytes) -> bytes:
        while terminator not in self._buffer:
            chunk = self._socket.recv(65536)
            if not chunk:
                break
            self._buffer += chunk
        index = self._buffer.find(terminator)
        if index < 0:
            found, self._buffer = self._buffer, b""
            return found
        end = index + len(terminator)
        found, self._buffer = self._buffer[:end], self._buffer[end:]
        return found


def connect(path: str, timeout: float = HANDSHAKE_TIMEOUT) -> Connection:
    """Open a Unix socket at ``path`` and complete the WebSocket handshake on it."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(path)
    connection = Connection(sock)
    connection.handshake()
    return connection


def _text_frame(text: str) -> bytes:
    return _frame(TEXT, text.encode())


def _frame(opcode: int, payload: bytes) -> bytes:
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    length = len(payload)
    if length < SHORT_LENGTH:
        header = struct.pack("!BB", FINAL_BIT | opcode, MASK_BIT | length)
    elif length < 65536:
        header = struct.pack("!BBH", FINAL_BIT | opcode, MASK_BIT | SHORT_LENGTH, length)
    else:
        header = struct.pack("!BBQ", FINAL_BIT | opcode, MASK_BIT | LONG_LENGTH, length)
    return header + mask + masked
