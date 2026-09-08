import socket
import struct
import threading

import pytest

from clownhead import websocket
from clownhead.websocket import Connection, ProtocolError

HANDSHAKE_OK = b"HTTP/1.1 101 Switching Protocols\r\nupgrade: websocket\r\n\r\n"


def server_frame(opcode: int, payload: bytes, final: bool = True) -> bytes:
    """One unmasked frame, the way a server sends them."""
    head = (0x80 if final else 0x00) | opcode
    length = len(payload)
    if length < 126:
        return struct.pack("!BB", head, length) + payload
    if length < 65536:
        return struct.pack("!BBH", head, 126, length) + payload
    return struct.pack("!BBQ", head, 127, length) + payload


def read_client_frame(sock: socket.socket) -> tuple[int, bytes]:
    """Read one masked client frame off a socket, returning its opcode and payload."""
    header = sock.recv(2)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", sock.recv(8))[0]
    mask = sock.recv(4)
    payload = b""
    while len(payload) < length:
        payload += sock.recv(length - len(payload))
    return opcode, bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))


@pytest.fixture
def pair() -> tuple[Connection, socket.socket]:
    """A connection whose peer is a plain socket the test drives by hand."""
    client, server = socket.socketpair()
    client.settimeout(2.0)
    server.settimeout(2.0)
    return Connection(client), server


def test_handshake_sends_the_upgrade_and_accepts_a_101(pair):
    connection, server = pair
    server.sendall(HANDSHAKE_OK)

    connection.handshake()

    request = server.recv(4096).decode()
    assert request.startswith("GET / HTTP/1.1\r\n")
    assert "Upgrade: websocket" in request
    assert "Sec-WebSocket-Version: 13" in request
    assert "Sec-WebSocket-Key: " in request


def test_handshake_refuses_anything_that_is_not_a_101(pair):
    connection, server = pair
    server.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")

    with pytest.raises(ProtocolError, match="400"):
        connection.handshake()


def test_handshake_refuses_a_peer_that_says_nothing(pair):
    connection, server = pair
    server.shutdown(socket.SHUT_WR)

    with pytest.raises(ProtocolError, match="no response"):
        connection.handshake()


def test_a_sent_message_arrives_masked(pair):
    connection, server = pair

    connection.send("hello")

    opcode, payload = read_client_frame(server)
    assert opcode == websocket.TEXT
    assert payload == b"hello"


@pytest.mark.parametrize("size", [10, 200, 70000])
def test_a_message_of_any_length_survives_the_round_trip(pair, size):
    """The three length encodings the specification has: one byte, two bytes, eight bytes."""
    connection, server = pair
    text = "x" * size
    read: list[bytes] = []

    reader = threading.Thread(target=lambda: read.append(read_client_frame(server)[1]))
    reader.start()
    connection.send(text)
    reader.join(timeout=5)

    assert read == [text.encode()]


def test_messages_are_read_off_the_wire_in_order(pair):
    connection, server = pair
    server.sendall(server_frame(websocket.TEXT, b"first") + server_frame(websocket.TEXT, b"second"))
    server.close()

    assert list(connection.messages()) == ["first", "second"]


def test_a_fragmented_message_is_reassembled(pair):
    connection, server = pair
    server.sendall(
        server_frame(websocket.TEXT, b"half ", final=False) + server_frame(websocket.CONTINUATION, b"a message")
    )
    server.close()

    assert list(connection.messages()) == ["half a message"]


def test_a_ping_is_answered_with_a_pong_and_left_out_of_the_messages(pair):
    connection, server = pair
    server.sendall(server_frame(websocket.PING, b"beat") + server_frame(websocket.TEXT, b"said"))

    assert list(connection.messages()) == ["said"]
    opcode, payload = read_client_frame(server)
    assert (opcode, payload) == (websocket.PONG, b"beat")


def test_a_close_frame_ends_the_conversation(pair):
    connection, server = pair
    server.sendall(server_frame(websocket.TEXT, b"last") + server_frame(websocket.CLOSE, b""))
    server.sendall(server_frame(websocket.TEXT, b"never read"))

    assert list(connection.messages()) == ["last"]


def test_binary_and_pong_frames_are_ignored(pair):
    connection, server = pair
    server.sendall(
        server_frame(websocket.BINARY, b"\x00\x01")
        + server_frame(websocket.PONG, b"")
        + server_frame(websocket.TEXT, b"spoken")
    )
    server.close()

    assert list(connection.messages()) == ["spoken"]


def test_a_frame_larger_than_the_client_will_read_stops_the_conversation(pair, monkeypatch):
    connection, server = pair
    monkeypatch.setattr(websocket, "MAX_FRAME_BYTES", 8)
    server.sendall(server_frame(websocket.TEXT, b"x" * 200))
    server.close()

    assert list(connection.messages()) == []


def test_a_truncated_frame_stops_the_conversation_rather_than_hanging(pair):
    connection, server = pair
    server.sendall(server_frame(websocket.TEXT, b"whole")[:4])
    server.close()

    assert list(connection.messages()) == []


def test_closing_sends_a_close_frame(pair):
    connection, server = pair

    connection.close()

    opcode, _ = read_client_frame(server)
    assert opcode == websocket.CLOSE


def test_closing_a_socket_the_peer_already_dropped_is_not_an_error(pair):
    connection, server = pair
    server.close()

    connection.close()


def test_the_context_manager_closes_on_the_way_out(pair):
    connection, server = pair

    with connection:
        connection.send("hi")

    read_client_frame(server)
    assert read_client_frame(server)[0] == websocket.CLOSE


def test_connect_dials_a_unix_socket_and_shakes_hands(socket_dir):
    path = socket_dir / "app.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)
    accepted: list[socket.socket] = []

    def serve() -> None:
        peer, _ = listener.accept()
        peer.recv(4096)
        peer.sendall(HANDSHAKE_OK)
        peer.sendall(server_frame(websocket.TEXT, b"ready"))
        accepted.append(peer)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with websocket.connect(str(path)) as connection:
            assert next(iter(connection.messages())) == "ready"
    finally:
        thread.join(timeout=2)
        for peer in accepted:
            peer.close()
        listener.close()


def test_connect_reports_a_socket_that_is_not_there(socket_dir):
    with pytest.raises(OSError):
        websocket.connect(str(socket_dir / "absent.sock"))
