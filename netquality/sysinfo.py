"""Local system / network introspection: ICMP ping, default gateway, link type, Wi-Fi signal.

Everything here is best-effort: any failure returns None / empty data instead of raising,
so the speed test still works when a probe is unavailable.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time

IS_WINDOWS = sys.platform.startswith("win")
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW: avoid console flashes


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        env = dict(os.environ, LC_ALL="C", LANG="C")
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env,
                             creationflags=_NO_WINDOW)
        for enc in ("utf-8", "mbcs" if IS_WINDOWS else "latin-1"):
            try:
                return res.stdout.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return res.stdout.decode("latin-1", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------- ICMP ping
class _IpOptionInformation(ctypes.Structure):
    _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]


class _IcmpEchoReply(ctypes.Structure):
    _fields_ = [("Address", ctypes.c_ulong), ("Status", ctypes.c_ulong),
                ("RoundTripTime", ctypes.c_ulong), ("DataSize", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                ("Options", _IpOptionInformation)]


class Pinger:
    """ICMP echo. On Windows uses IcmpSendEcho (no admin rights needed); elsewhere the
    system `ping` binary. `ping(timeout_ms, size)` returns RTT in ms or None on loss.

    On Windows the RTT is timed with perf_counter around the call, because the API only
    reports whole milliseconds, which is too coarse for LAN/loopback hops."""

    MAX_SIZE = 65500

    def __init__(self, host: str):
        self.host = host
        self.ip = socket.gethostbyname(host)
        self._handle = None
        if IS_WINDOWS:
            self._api = ctypes.WinDLL("iphlpapi")
            self._api.IcmpCreateFile.restype = ctypes.c_void_p
            self._api.IcmpSendEcho.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
                                              ctypes.c_ushort, ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.c_ulong, ctypes.c_ulong]
            self._api.IcmpSendEcho.restype = ctypes.c_ulong
            self._api.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
            self._handle = self._api.IcmpCreateFile()
            self._addr = int.from_bytes(socket.inet_aton(self.ip), "little")
            pattern = (b"netquality-probe-" * (self.MAX_SIZE // 17 + 1))[:self.MAX_SIZE]
            self._payload = (ctypes.c_char * self.MAX_SIZE).from_buffer_copy(pattern)
            self._reply_size = ctypes.sizeof(_IcmpEchoReply) + self.MAX_SIZE + 8 + 64
            self._reply = ctypes.create_string_buffer(self._reply_size)

    def ping(self, timeout_ms: int = 1000, size: int = 32) -> float | None:
        size = max(1, min(self.MAX_SIZE, size))
        if IS_WINDOWS:
            t0 = time.perf_counter()
            n = self._api.IcmpSendEcho(self._handle, self._addr, self._payload, size, None,
                                       self._reply, self._reply_size, timeout_ms)
            dt = (time.perf_counter() - t0) * 1000
            if n == 0:
                return None
            reply = _IcmpEchoReply.from_buffer(self._reply)
            if reply.Status != 0:
                return None
            # perf_counter includes ~0.05 ms of API overhead; never report less than the API says
            return max(dt - 0.05, float(reply.RoundTripTime), 0.01)
        flag = ["-W", str(timeout_ms)] if sys.platform == "darwin" else ["-W", str(max(1, timeout_ms // 1000))]
        out = _run(["ping", "-c", "1", "-s", str(size), *flag, self.ip], timeout=timeout_ms / 1000 + 2)
        m = re.search(r"time[=<]([\d.]+)", out)
        return float(m.group(1)) if m else None

    def close(self):
        if IS_WINDOWS and self._handle:
            self._api.IcmpCloseHandle(self._handle)
            self._handle = None


# --------------------------------------------------------------------------- local link
def _parse_link_speed(text: str | None) -> float | None:
    """'1 Gbps' / '866.7 Mbps' -> Mbps."""
    if not text:
        return None
    m = re.search(r"([\d.,]+)\s*([KMG])bps", str(text), re.I)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    return val * {"K": 1e-3, "M": 1.0, "G": 1000.0}[m.group(2).upper()]


def parse_netsh_wlan(text: str) -> dict:
    """Parse `netsh wlan show interfaces` heuristically (works with localized output:
    it keys off units/values rather than translated labels where possible)."""
    info: dict = {}
    mbps_vals = []
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key_l, val = key.strip().lower(), val.strip()
        if key_l == "ssid":
            info["ssid"] = val
        elif key_l == "rssi":
            try:
                info["rssi_dbm"] = int(val)
            except ValueError:
                pass
        elif re.fullmatch(r"\d{1,3}\s*%", val):
            info["signal_pct"] = int(val.rstrip("% "))
        elif "(mbps)" in key_l:
            try:
                mbps_vals.append(float(val.replace(",", ".")))
            except ValueError:
                pass
        elif re.fullmatch(r"[\d.,]+\s*GHz", val, re.I):
            info["band"] = val
        elif re.fullmatch(r"802\.11\w+", val):
            info["radio"] = val
        elif key_l.startswith(("channel", "canal", "kanal")) and val.isdigit():
            info["channel"] = int(val)
    if mbps_vals:
        info["rx_rate_mbps"] = mbps_vals[0]
        if len(mbps_vals) > 1:
            info["tx_rate_mbps"] = mbps_vals[1]
    return info


def link_info() -> dict:
    """Default gateway, adapter name/type/link speed, and Wi-Fi details if applicable."""
    info: dict = {"gateway": None, "adapter": None, "medium": "unknown", "link_mbps": None, "wifi": None}
    try:
        if IS_WINDOWS:
            ps = ("$r = Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | "
                  "Sort-Object RouteMetric | Select-Object -First 1; "
                  "$a = Get-NetAdapter -InterfaceIndex $r.ifIndex; "
                  "[pscustomobject]@{gw=$r.NextHop; name=$a.Name; desc=$a.InterfaceDescription; "
                  "media=[string]$a.PhysicalMediaType; speed=[string]$a.LinkSpeed} | ConvertTo-Json -Compress")
            out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=15)
            data = json.loads(out) if out.strip() else {}
            info["gateway"] = data.get("gw")
            info["adapter"] = data.get("desc") or data.get("name")
            info["link_mbps"] = _parse_link_speed(data.get("speed"))
            media = (data.get("media") or "").lower()
            if "802.11" in media or "wireless" in media:
                info["medium"] = "wifi"
                info["wifi"] = parse_netsh_wlan(_run(["netsh", "wlan", "show", "interfaces"]))
                if info["wifi"].get("rx_rate_mbps"):
                    info["link_mbps"] = info["wifi"]["rx_rate_mbps"]
            elif "802.3" in media:
                info["medium"] = "ethernet"
            elif media:
                info["medium"] = media
        elif sys.platform == "darwin":
            out = _run(["route", "-n", "get", "default"])
            m = re.search(r"gateway:\s*(\S+)", out)
            info["gateway"] = m.group(1) if m else None
            m = re.search(r"interface:\s*(\S+)", out)
            info["adapter"] = m.group(1) if m else None
        else:
            out = _run(["ip", "route", "show", "default"])
            m = re.search(r"default via (\S+) dev (\S+)", out)
            if m:
                info["gateway"], dev = m.group(1), m.group(2)
                info["adapter"] = dev
                if os.path.isdir(f"/sys/class/net/{dev}/wireless"):
                    info["medium"] = "wifi"
                    iw = _run(["iw", "dev", dev, "link"])
                    wifi: dict = {}
                    if (s := re.search(r"signal:\s*(-?\d+)", iw)):
                        wifi["rssi_dbm"] = int(s.group(1))
                    if (s := re.search(r"rx bitrate:\s*([\d.]+)", iw)):
                        wifi["rx_rate_mbps"] = float(s.group(1))
                    if (s := re.search(r"tx bitrate:\s*([\d.]+)", iw)):
                        wifi["tx_rate_mbps"] = float(s.group(1))
                    if (s := re.search(r"freq:\s*(\d+)", iw)):
                        wifi["band"] = f"{int(s.group(1)) / 1000:.1f} GHz"
                    if (s := re.search(r"SSID:\s*(.+)", iw)):
                        wifi["ssid"] = s.group(1).strip()
                    info["wifi"] = wifi
                    info["link_mbps"] = wifi.get("rx_rate_mbps")
                else:
                    info["medium"] = "ethernet"
                    try:
                        with open(f"/sys/class/net/{dev}/speed") as f:
                            info["link_mbps"] = float(f.read().strip())
                    except (OSError, ValueError):
                        pass
    except Exception:  # best-effort probe
        pass
    return info
