"""Local layers: this PC's own network stack (loopback) and the hop to the router.

* Localhost: a TCP sink/echo server on 127.0.0.1 inside this process. Throughput is what an
  application can push through the OS TCP/IP stack, the ceiling for anything network-related
  on this PC. Ping-pong latency shows the stack + scheduler overhead.
* Router: ICMP echoes of increasing size. The *minimum* RTT grows linearly with packet size;
  the slope is the serialization time, which gives an estimate of the link capacity.
  Minimum RTT is immune to the ICMP rate limiting most routers apply, unlike a ping flood.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from . import stats
from .sysinfo import Pinger

ROUTER_SIZES = [32, 1400, 8000, 16000, 32000, 48000, 65000]


class _LoopbackServer:
    """Accepts connections; first byte selects mode: b'S' = sink, b'E' = echo."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        self.received = 0
        self._lock = threading.Lock()
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket):
        try:
            mode = conn.recv(1)
            if mode == b"S":
                buf = bytearray(1 << 20)
                while n := conn.recv_into(buf):
                    with self._lock:
                        self.received += n
            elif mode == b"E":
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                while data := conn.recv(64):
                    conn.sendall(data)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self._closed = True
        self.sock.close()


def _loopback_throughput(server: _LoopbackServer, streams: int, seconds: float) -> float:
    payload = b"\0" * (1 << 20)
    deadline = time.perf_counter() + seconds
    socks = []
    for _ in range(streams):
        s = socket.create_connection(("127.0.0.1", server.port))
        s.sendall(b"S")
        socks.append(s)

    def pump(s):
        try:
            while time.perf_counter() < deadline:
                s.sendall(payload)
        except OSError:
            pass

    with server._lock:
        start_bytes = server.received
    t0 = time.perf_counter()
    threads = [threading.Thread(target=pump, args=(s,), daemon=True) for s in socks]
    for t in threads:
        t.start()
    for t in threads:
        t.join(seconds + 2)
    with server._lock:
        got = server.received - start_bytes
    elapsed = time.perf_counter() - t0
    for s in socks:
        s.close()
    return got * 8 / elapsed / 1e6


def _loopback_rtt(server: _LoopbackServer, count: int) -> list[float]:
    s = socket.create_connection(("127.0.0.1", server.port))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.sendall(b"E")
    out = []
    try:
        for _ in range(count):
            t0 = time.perf_counter()
            s.sendall(b"x")
            s.recv(64)
            out.append((time.perf_counter() - t0) * 1e6)  # microseconds
    finally:
        s.close()
    return out


def measure_localhost(streams: int = 4, seconds: float = 1.5,
                      check: Callable[[], None] = lambda: None) -> dict:
    res: dict = {}
    server = _LoopbackServer()
    try:
        res["tcp_rtt_us"] = stats.summarize(_loopback_rtt(server, 2000))
        check()
        res["tcp_1_stream_mbps"] = _loopback_throughput(server, 1, seconds)
        check()
        res["tcp_multi_stream_mbps"] = _loopback_throughput(server, streams, seconds)
        res["streams"] = streams
    except OSError as e:
        res["error"] = str(e)
    finally:
        server.close()
    try:
        p = Pinger("127.0.0.1")
        icmp = [p.ping(500) for _ in range(20)]
        p.close()
        res["icmp_ms"] = stats.summarize([x for x in icmp if x is not None])
    except OSError:
        pass
    return res


def measure_router(gateway: str, per_size: int = 10,
                   check: Callable[[], None] = lambda: None,
                   progress: Callable[[float], None] = lambda f: None) -> dict:
    """Latency/loss by packet size, plus a capacity estimate from the RTT-vs-size slope."""
    try:
        p = Pinger(gateway)
    except OSError as e:
        return {"error": str(e)}
    sizes = []
    try:
        for i, size in enumerate(ROUTER_SIZES):
            samples = []
            for _ in range(per_size):
                check()
                samples.append(p.ping(400, size))
                time.sleep(0.01)
            ok = [x for x in samples if x is not None]
            sizes.append({
                "size": size, "sent": len(samples), "received": len(ok),
                "loss_pct": 100.0 * (len(samples) - len(ok)) / len(samples),
                "min_ms": min(ok) if ok else None,
                "median_ms": stats.percentile(ok, 50) if ok else None,
            })
            progress((i + 1) / len(ROUTER_SIZES))
    finally:
        p.close()
    pts = [(s["size"], s["min_ms"]) for s in sizes if s["min_ms"] is not None]
    fit = stats.linear_fit(pts)
    capacity = None
    if fit and fit["slope"] > 0:
        # extra bytes cross the link twice (request + reply): 16 bits per byte of payload
        capacity = 0.016 / fit["slope"]  # Mbps, since slope is ms/byte
    big = next((s for s in reversed(sizes) if s["median_ms"]), None)
    return {
        "target": gateway,
        "sizes": sizes,
        "fit": fit,
        "capacity_mbps": capacity,
        "capacity_reliable": bool(fit and fit["r2"] >= 0.85 and capacity and capacity > 0),
        "base_rtt_ms": sizes[0]["min_ms"] if sizes else None,
        "large_packet_roundtrip_mbps": (big["size"] * 16 / big["median_ms"] / 1000) if big else None,
        "large_packet_loss_pct": (sum(s["sent"] - s["received"] for s in sizes if s["size"] >= 16000) /
                                  max(1, sum(s["sent"] for s in sizes if s["size"] >= 16000)) * 100),
        "small_packet_loss_pct": sizes[0]["loss_pct"] if sizes else None,
    }
