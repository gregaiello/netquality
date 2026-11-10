"""Measurement engine. Pure Python (stdlib only), reports progress through a callback.

Measurement target: Cloudflare's public speed-test endpoints (speed.cloudflare.com), the same
ones used by https://speed.cloudflare.com — no ads, no account, anycast to the nearest PoP.
"""
from __future__ import annotations

import http.client
import random
import socket
import ssl
import string
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable

from . import stats
from .local import measure_localhost, measure_router
from .sysinfo import Pinger, link_info

HOST = "speed.cloudflare.com"
UA = "netquality/1.0 (local speed test)"
Emit = Callable[[str, dict], None]


class Cancelled(Exception):
    pass


@dataclass
class TestConfig:
    download_seconds: float = 10.0
    upload_seconds: float = 10.0
    streams: int = 6
    warmup_seconds: float = 2.0       # excluded from "sustained" speed (TCP slow start)
    latency_probes: int = 20
    icmp_probes: int = 30
    icmp_target: str = "1.1.1.1"
    loaded_probe_interval: float = 0.25


PHASES = [  # name, label, weight of total progress
    ("setup", "Locating server & inspecting link", 0.05),
    ("local", "Testing this PC (localhost) & router hop", 0.12),
    ("latency", "Measuring idle internet latency", 0.08),
    ("dns", "Measuring DNS & reachability", 0.03),
    ("download", "Testing download", 0.36),
    ("upload", "Testing upload", 0.36),
]


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _conn(timeout: float = 10.0) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(HOST, 443, timeout=timeout, context=_ctx())


def _headers() -> dict:
    return {"User-Agent": UA, "Accept-Encoding": "identity"}


class SpeedTest:
    def __init__(self, config: TestConfig, emit: Emit):
        self.cfg = config
        self.emit = emit
        self._cancel = threading.Event()
        self._bytes_used = 0
        self._bytes_lock = threading.Lock()

    # ------------------------------------------------------------------ control
    def cancel(self):
        self._cancel.set()

    def _check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def _sleep(self, seconds: float):
        if self._cancel.wait(seconds):
            raise Cancelled()

    def _log(self, msg: str):
        self.emit("log", {"msg": msg})

    def _progress(self, phase: str, frac: float):
        done = 0.0
        for name, _, w in PHASES:
            if name == phase:
                self.emit("progress", {"value": min(1.0, done + w * max(0.0, min(1.0, frac)))})
                return
            done += w

    def _phase(self, name: str):
        label = next(l for n, l, _ in PHASES if n == name)
        self.emit("phase", {"name": name, "label": label})
        self._progress(name, 0)
        self._log(label + "…")

    def _account(self, n: int):
        with self._bytes_lock:
            self._bytes_used += n

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        r: dict = {"timestamp": datetime.now().isoformat(timespec="seconds"),
                   "config": asdict(self.cfg), "errors": []}
        t_start = time.perf_counter()

        self._phase("setup")
        r["link"] = link_info()
        self._log(f"Local link: {r['link'].get('medium')} via {r['link'].get('adapter')}, "
                  f"gateway {r['link'].get('gateway')}")
        self._progress("setup", 0.5)
        r["connection"] = self._connection_breakdown()
        r["meta"] = self._meta()
        self.emit("meta", r["meta"])
        self._progress("setup", 1)

        self._phase("local")
        r["localhost"] = measure_localhost(check=self._check)
        lh = r["localhost"]
        if lh.get("tcp_multi_stream_mbps"):
            self._log(f"Localhost: {lh['tcp_1_stream_mbps'] / 1000:.1f} Gbps (1 stream), "
                      f"{lh['tcp_multi_stream_mbps'] / 1000:.1f} Gbps ({lh['streams']} streams), "
                      f"TCP RTT {lh['tcp_rtt_us']['median']:.0f} µs")
        self._progress("local", 0.3)
        gw = r["link"].get("gateway")
        if gw:
            r["router"] = measure_router(gw, check=self._check,
                                         progress=lambda f: self._progress("local", 0.3 + 0.7 * f))
            rt = r["router"]
            if rt.get("capacity_mbps"):
                self._log(f"Router {gw}: base RTT {rt['base_rtt_ms']:.2f} ms, estimated link capacity "
                          f"{rt['capacity_mbps']:.0f} Mbps (fit R² {rt['fit']['r2']:.2f})")
        else:
            r["router"] = {"error": "no default gateway found"}
        self.emit("local", {"localhost": r["localhost"], "router": r["router"]})
        self._progress("local", 1)

        self._phase("latency")
        r["latency"] = self._idle_latency(r["link"].get("gateway"))
        self.emit("idle_latency", r["latency"])

        self._phase("dns")
        r["dns"] = self._dns()
        r["targets"] = self._tcp_targets()
        self._progress("dns", 1)

        self._phase("download")
        r["download"] = self._throughput("download", self.cfg.download_seconds, r["link"].get("gateway"))
        self._phase("upload")
        r["upload"] = self._throughput("upload", self.cfg.upload_seconds, r["link"].get("gateway"))

        idle = (r["latency"].get("http") or {}).get("median")
        for d in ("download", "upload"):
            loaded = (r[d].get("loaded_latency") or {}).get("median")
            inc = (loaded - idle) if (loaded is not None and idle is not None) else None
            r[d]["bufferbloat_ms"] = max(0.0, inc) if inc is not None else None
            r[d]["bufferbloat_grade"] = stats.bufferbloat_grade(r[d]["bufferbloat_ms"])
        r["data_used_mb"] = self._bytes_used / 1e6
        r["duration_s"] = time.perf_counter() - t_start
        self.emit("progress", {"value": 1.0})
        return r

    # ------------------------------------------------------------------ setup
    def _connection_breakdown(self) -> dict:
        """Time each step of opening an HTTPS connection: DNS, TCP, TLS, first byte."""
        out: dict = {}
        try:
            t0 = time.perf_counter()
            infos = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
            t1 = time.perf_counter()
            family, _, _, _, addr = infos[0]
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(addr)
            t2 = time.perf_counter()
            ssock = _ctx().wrap_socket(sock, server_hostname=HOST)
            t3 = time.perf_counter()
            ssock.sendall(f"GET /__down?bytes=0 HTTP/1.1\r\nHost: {HOST}\r\nUser-Agent: {UA}\r\n"
                          f"Connection: close\r\n\r\n".encode())
            first = ssock.recv(1)
            t4 = time.perf_counter()
            rest = b""
            tls_version, cipher = ssock.version(), (ssock.cipher() or [None])[0]
            while chunk := ssock.recv(65536):
                rest += chunk
            ssock.close()
            out = {
                "dns_ms": (t1 - t0) * 1000, "tcp_ms": (t2 - t1) * 1000,
                "tls_ms": (t3 - t2) * 1000, "ttfb_ms": (t4 - t3) * 1000,
                "total_ms": (t4 - t0) * 1000,
                "remote_ip": addr[0], "ip_version": 6 if family == socket.AF_INET6 else 4,
                "tls_version": tls_version, "cipher": cipher,
                "ipv6_available": any(i[0] == socket.AF_INET6 for i in infos),
            }
            if not first:
                out["warning"] = "empty response"
        except OSError as e:
            out["error"] = str(e)
            self._log(f"Connection breakdown failed: {e}")
        return out

    def _meta(self) -> dict:
        meta: dict = {}
        try:
            c = _conn()
            c.request("GET", "/__down?bytes=0", headers=_headers())
            resp = c.getresponse()
            resp.read()
            for k in ("ip", "colo", "asn", "country", "city", "timezone"):
                v = resp.getheader(f"cf-meta-{k}")
                if v:
                    meta[k] = v
            c.request("GET", "/cdn-cgi/trace", headers=_headers())
            resp = c.getresponse()
            for line in resp.read().decode("utf-8", "replace").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k in ("http", "tls", "loc", "warp", "colo", "ip"):
                        meta.setdefault(k, v)
            c.close()
        except (OSError, http.client.HTTPException) as e:
            self._log(f"Metadata lookup failed: {e}")
        return meta

    # ------------------------------------------------------------------ latency
    @staticmethod
    def _http_probe(conn: http.client.HTTPSConnection) -> tuple[float, dict]:
        """One request/response on a warm connection. Returns (rtt_ms, cfL4 stats).

        The server reports its own processing time (Server-Timing cfSpeedWorker, ~20 ms);
        it is subtracted so the value reflects the network round trip. This matches the TCP
        RTT the server kernel observes (cfL4 rtt), like speed.cloudflare.com does."""
        t0 = time.perf_counter()
        conn.request("GET", "/__down?bytes=0", headers=_headers())
        resp = conn.getresponse()
        resp.read()
        rtt = (time.perf_counter() - t0) * 1000
        l4, server_ms = {}, 0.0
        for h in resp.headers.get_all("server-timing") or []:
            parsed = stats.parse_server_timing(h)
            if "cfL4" in parsed:
                l4 = stats.parse_cf_l4(parsed["cfL4"].get("desc"))
            for name in ("cfSpeedWorker", "cfRequestDuration"):
                if name in parsed and "dur" in parsed[name]:
                    server_ms = max(server_ms, parsed[name]["dur"])
        return max(0.1, rtt - server_ms), l4

    def _icmp_series(self, host: str, count: int, interval: float, out: dict, stop: threading.Event | None = None):
        try:
            pinger = Pinger(host)
        except OSError as e:
            out["error"] = str(e)
            return
        samples: list[float | None] = []
        try:
            for _ in range(count):
                if self._cancel.is_set() or (stop and stop.is_set()):
                    break
                t0 = time.perf_counter()
                samples.append(pinger.ping(1000))
                rest = interval - (time.perf_counter() - t0)
                if rest > 0:
                    time.sleep(rest)
        finally:
            pinger.close()
        out.update(self._icmp_summary(host, samples))

    @staticmethod
    def _icmp_summary(host: str, samples: list) -> dict:
        ok = [s for s in samples if s is not None]
        return {
            "target": host, "sent": len(samples), "received": len(ok),
            "loss_pct": (100.0 * (len(samples) - len(ok)) / len(samples)) if samples else None,
            "summary": stats.summarize(ok), "jitter_ms": stats.jitter(ok), "samples": samples,
        }

    def _idle_latency(self, gateway: str | None) -> dict:
        res: dict = {"http": None, "icmp": {}, "gateway": {}}
        threads = [threading.Thread(target=self._icmp_series,
                                    args=(self.cfg.icmp_target, self.cfg.icmp_probes, 0.1, res["icmp"]),
                                    daemon=True)]
        if gateway:
            threads.append(threading.Thread(target=self._icmp_series,
                                            args=(gateway, self.cfg.icmp_probes, 0.1, res["gateway"]),
                                            daemon=True))
        for t in threads:
            t.start()

        samples, srv_rtt, failures = [], [], 0
        conn = None
        for i in range(self.cfg.latency_probes):
            self._check()
            try:
                if conn is None:
                    conn = _conn(timeout=4)
                    self._http_probe(conn)  # warm-up: TCP+TLS setup is not counted
                rtt, l4 = self._http_probe(conn)
            except (OSError, http.client.HTTPException) as e:
                failures += 1
                self._log(f"HTTP latency probe {i + 1} failed ({type(e).__name__}), reconnecting")
                if conn:
                    conn.close()
                conn = None
                continue
            samples.append(rtt)
            if "rtt" in l4:
                srv_rtt.append(l4["rtt"] / 1000.0)
            self.emit("latency_sample", {"kind": "idle", "i": i, "ms": rtt})
            self._progress("latency", 0.8 * (i + 1) / self.cfg.latency_probes)
            time.sleep(0.05)
        if conn:
            conn.close()
        res["http_failures"] = failures
        for t in threads:
            while t.is_alive():
                self._check()
                t.join(0.2)
        self._progress("latency", 1)
        res["http"] = stats.summarize(samples)
        if res["http"]:
            res["http"]["jitter_ms"] = stats.jitter(samples)
        res["http_samples"] = samples
        res["server_tcp_rtt_ms"] = stats.summarize(srv_rtt)
        icmp = res["icmp"]
        if icmp.get("summary"):
            self._log(f"ICMP {icmp['target']}: median {icmp['summary']['median']:.1f} ms, "
                      f"loss {icmp['loss_pct']:.1f}%")
        return res

    # ------------------------------------------------------------------ dns / reachability
    def _dns(self) -> dict:
        names = ["www.google.com", "www.wikipedia.org", "www.microsoft.com", "www.amazon.com"]
        cached = []
        for n in names:
            self._check()
            t0 = time.perf_counter()
            try:
                socket.getaddrinfo(n, 443, type=socket.SOCK_STREAM)
                cached.append((time.perf_counter() - t0) * 1000)
            except OSError:
                pass
        # A random sub-domain can't be cached anywhere: forces a full recursive lookup.
        uncached = []
        for _ in range(3):
            self._check()
            rnd = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))
            t0 = time.perf_counter()
            try:
                socket.getaddrinfo(f"{rnd}.cloudflare.com", 443, type=socket.SOCK_STREAM)
            except OSError:
                pass  # NXDOMAIN is expected and still measures the resolver round trip
            uncached.append((time.perf_counter() - t0) * 1000)
        self._progress("dns", 0.5)
        return {"popular_ms": stats.summarize(cached), "uncached_ms": stats.summarize(uncached)}

    def _tcp_targets(self) -> list[dict]:
        targets = [("Cloudflare DNS", "1.1.1.1"), ("Google DNS", "8.8.8.8"),
                   ("Quad9 DNS", "9.9.9.9"), ("Google", "www.google.com"),
                   ("Microsoft", "www.microsoft.com"), ("Amazon", "www.amazon.com")]
        out = []
        for name, host in targets:
            self._check()
            times = []
            for _ in range(3):
                try:
                    ip = socket.gethostbyname(host)
                    t0 = time.perf_counter()
                    with socket.create_connection((ip, 443), timeout=3):
                        times.append((time.perf_counter() - t0) * 1000)
                except OSError:
                    pass
            out.append({"name": name, "host": host,
                        "median_ms": stats.percentile(times, 50) if times else None,
                        "min_ms": min(times) if times else None})
        return out

    # ------------------------------------------------------------------ throughput
    def _throughput(self, direction: str, seconds: float, gateway: str | None) -> dict:
        cfg = self.cfg
        counter = [0]
        lock = threading.Lock()
        stop = threading.Event()
        errors: list[str] = []
        l4_by_conn: dict[int, dict] = {}
        payload = random.randbytes(1 << 20) if direction == "upload" else b""

        def add(n: int):
            with lock:
                counter[0] += n

        def dl_worker(wid: int):
            size = 1_000_000          # grow request size like speed.cloudflare.com does
            conn = None
            while not stop.is_set() and not self._cancel.is_set():
                try:
                    if conn is None:
                        conn = _conn(timeout=15)
                    conn.request("GET", f"/__down?bytes={size}", headers=_headers())
                    resp = conn.getresponse()
                    for h in resp.headers.get_all("server-timing") or []:
                        p = stats.parse_server_timing(h)
                        if "cfL4" in p:
                            l4_by_conn.setdefault(wid, {}).update(stats.parse_cf_l4(p["cfL4"].get("desc")))
                    while True:
                        if stop.is_set() or self._cancel.is_set():
                            conn.close()
                            return
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        add(len(chunk))
                    size = min(size * 2, 100_000_000)
                except (OSError, http.client.HTTPException) as e:
                    errors.append(f"{type(e).__name__}: {e}")
                    if conn:
                        conn.close()
                    conn = None
                    if stop.wait(0.3):
                        return

        def ul_worker(wid: int):
            size = 1_000_000
            conn = None
            while not stop.is_set() and not self._cancel.is_set():
                aborted = [False]

                def body(total=size):
                    sent = 0
                    while sent < total:
                        if stop.is_set() or self._cancel.is_set():
                            aborted[0] = True
                            return
                        n = min(65536, total - sent)
                        yield payload[:n]
                        add(n)  # counted after the previous chunk was handed to the socket
                        sent += n
                try:
                    if conn is None:
                        conn = _conn(timeout=15)
                    hdr = dict(_headers(), **{"Content-Type": "application/octet-stream",
                                              "Content-Length": str(size)})
                    conn.request("POST", "/__up", body=body(), headers=hdr)
                    if aborted[0]:
                        conn.close()
                        return
                    conn.getresponse().read()
                    size = min(size * 2, 50_000_000)
                except (OSError, http.client.HTTPException) as e:
                    if not (stop.is_set() or self._cancel.is_set()):
                        errors.append(f"{type(e).__name__}: {e}")
                    if conn:
                        conn.close()
                    conn = None
                    if stop.wait(0.3):
                        return

        # Loaded-latency probes on separate connections, sharing the saturated link.
        loaded: list[tuple[float, float]] = []
        gw_loaded: dict = {}
        t_start = time.perf_counter()

        def latency_worker():
            conn = None
            while not stop.is_set() and not self._cancel.is_set():
                try:
                    if conn is None:
                        conn = _conn(timeout=5)
                        self._http_probe(conn)  # connection setup excluded
                    rtt, _ = self._http_probe(conn)
                    t = time.perf_counter() - t_start
                    loaded.append((t, rtt))
                    self.emit("latency_sample", {"kind": direction, "t": t, "ms": rtt})
                except (OSError, http.client.HTTPException):
                    if conn:
                        conn.close()
                    conn = None
                stop.wait(cfg.loaded_probe_interval)
            if conn:
                conn.close()

        worker = dl_worker if direction == "download" else ul_worker
        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(cfg.streams)]
        threads.append(threading.Thread(target=latency_worker, daemon=True))
        if gateway:
            n_gw = int(seconds / 0.25)
            threads.append(threading.Thread(target=self._icmp_series,
                                            args=(gateway, n_gw, 0.25, gw_loaded, stop), daemon=True))
        for t in threads:
            t.start()

        cum: list[tuple[float, int]] = [(0.0, 0)]
        interval = 0.1
        try:
            while True:
                self._sleep(interval)
                now = time.perf_counter() - t_start
                with lock:
                    b = counter[0]
                cum.append((now, b))
                # live value: rate over the last ~1 s
                ref = next((s for s in cum if s[0] >= now - 1.0), cum[0])
                live = (b - ref[1]) * 8 / max(1e-6, now - ref[0]) / 1e6
                self.emit("throughput", {"direction": direction, "t": now, "mbps": live})
                self._progress(direction, now / seconds)
                if now >= seconds:
                    break
        finally:
            stop.set()
            for t in threads:
                t.join(2.0)
        self._account(cum[-1][1])

        # ---- analysis
        warm = cfg.warmup_seconds
        after = [s for s in cum if s[0] >= warm]
        if len(after) >= 2:
            sustained = (after[-1][1] - after[0][1]) * 8 / (after[-1][0] - after[0][0]) / 1e6
        else:
            sustained = cum[-1][1] * 8 / max(1e-6, cum[-1][0]) / 1e6
        windows = stats.windowed_rates(cum, 0.5)
        win_after = [m for t, m in windows if t > warm]
        overall = cum[-1][1] * 8 / max(1e-6, cum[-1][0]) / 1e6
        # time to reach 90% of sustained speed (how quickly the link ramps up)
        ramp = next((t for t, m in windows if sustained > 0 and m >= 0.9 * sustained), None)

        l4_tot = {"sent": 0, "retrans": 0, "lost": 0}
        rtts = []
        for d in l4_by_conn.values():
            for k in l4_tot:
                l4_tot[k] += int(d.get(k, 0) or 0)
            if d.get("rtt"):
                rtts.append(d["rtt"] / 1000)
        retrans_pct = (100 * l4_tot["retrans"] / l4_tot["sent"]) if l4_tot["sent"] > 50 else None

        loaded_ms = [m for _, m in loaded]
        res = {
            "mbps": sustained,
            "mbps_overall": overall,
            "peak_mbps": max(win_after) if win_after else (max((m for _, m in windows), default=None)),
            "rates": stats.summarize(win_after),
            "stability": stats.stability_score(win_after),
            "ramp_up_s": ramp,
            "bytes": cum[-1][1],
            "seconds": cum[-1][0],
            "streams": cfg.streams,
            "errors": len(errors),
            "error_samples": errors[:5],
            "series": [(round(t, 2), round(m, 3)) for t, m in windows],
            "loaded_latency": stats.summarize(loaded_ms),
            "loaded_jitter_ms": stats.jitter(loaded_ms),
            "loaded_samples": [(round(t, 2), round(m, 2)) for t, m in loaded],
            "gateway_loaded": gw_loaded or None,
            "server_tcp": {"retrans_pct": retrans_pct, **l4_tot,
                           "rtt_ms": stats.percentile(rtts, 50) if rtts else None} if l4_by_conn else None,
        }
        self._log(f"{direction.title()}: {sustained:.1f} Mbps sustained, peak {res['peak_mbps'] or 0:.1f} Mbps, "
                  f"{len(errors)} errors")
        if errors:
            self._log(f"  first error: {errors[0]}")
        return res
