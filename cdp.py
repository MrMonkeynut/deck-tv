"""Tiny Chrome DevTools client (standard library only) for the browser apps Deck TV opens.

Movy, YouTube and UFC run with --remote-debugging-port on 127.0.0.1, so Deck TV can read and
fill their search boxes directly instead of guessing with key presses, and see what's playing.
"""
import base64
import json
import os
import socket
import struct
import urllib.request
from urllib.parse import urlparse


def targets(port, kinds=("page",)):
    """The app's pages (and, if asked, the embedded frames that run in their own process)."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
        return [t for t in json.load(r) if t.get("type") in kinds and t.get("webSocketDebuggerUrl")]


def page_target(port):
    """The app's visible page (its first 'page' target)."""
    return targets(port)[0]


class Page:
    def __init__(self, port, target=None, timeout=5):
        url = urlparse((target or page_target(port))["webSocketDebuggerUrl"])
        self.sock = socket.create_connection((url.hostname, url.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET {url.path} HTTP/1.1\r\nHost: {url.hostname}:{url.port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed the connection")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buffer = head.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ---- websocket framing (client frames are masked) ----
    def _send(self, text):
        data = text.encode()
        header = bytes([0x81])
        n = len(data)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("DevTools closed the connection")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def _recv(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if b1 & 0x0F == 0x9:  # ping
                continue
            message += payload
            if b1 & 0x80:
                return message.decode(errors="replace")

    # ---- DevTools commands ----
    def call(self, method, **params):
        self.next_id += 1
        self._send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            reply = json.loads(self._recv())
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    raise RuntimeError(reply["error"].get("message"))
                return reply.get("result", {})

    def eval(self, expression, gesture=False):
        """gesture=True runs it as if from a click, which play() can need."""
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True,
                           userGesture=gesture)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("text", "script error"))
        return result.get("result", {}).get("value")
