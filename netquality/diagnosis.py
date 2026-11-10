"""Turn raw results into human insights: activity suitability, detected issues and fixes.

Pure functions over the result dict produced by engine.SpeedTest.run().
Severity levels: "critical", "warning", "info", "good".
"""
from __future__ import annotations

from .stats import human_duration

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "good": 3}


def _g(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict) or d.get(p) is None:
            return default
        d = d[p]
    return d


def key_numbers(r: dict) -> dict:
    """Flatten the headline numbers used by the rules (None when unavailable)."""
    return {
        "down": _g(r, "download", "mbps"),
        "up": _g(r, "upload", "mbps"),
        "idle_ms": _g(r, "latency", "http", "median"),
        "idle_jitter": _g(r, "latency", "http", "jitter_ms"),
        "icmp_ms": _g(r, "latency", "icmp", "summary", "median"),
        "icmp_jitter": _g(r, "latency", "icmp", "jitter_ms"),
        "loss": _g(r, "latency", "icmp", "loss_pct"),
        "gw_ms": _g(r, "latency", "gateway", "summary", "median"),
        "gw_p95": _g(r, "latency", "gateway", "summary", "p95"),
        "gw_jitter": _g(r, "latency", "gateway", "jitter_ms"),
        "gw_loss": _g(r, "latency", "gateway", "loss_pct"),
        "dl_loaded": _g(r, "download", "loaded_latency", "median"),
        "ul_loaded": _g(r, "upload", "loaded_latency", "median"),
        "dl_bloat": _g(r, "download", "bufferbloat_ms"),
        "ul_bloat": _g(r, "upload", "bufferbloat_ms"),
        "dl_gw_loaded": _g(r, "download", "gateway_loaded", "summary", "median"),
        "ul_gw_loaded": _g(r, "upload", "gateway_loaded", "summary", "median"),
        "dl_gw_loss": _g(r, "download", "gateway_loaded", "loss_pct"),
        "medium": _g(r, "link", "medium", default="unknown"),
        "link_mbps": _g(r, "link", "link_mbps"),
        "rssi": _g(r, "link", "wifi", "rssi_dbm"),
        "signal_pct": _g(r, "link", "wifi", "signal_pct"),
        "band": _g(r, "link", "wifi", "band"),
        "radio": _g(r, "link", "wifi", "radio"),
        "dns_uncached": _g(r, "dns", "uncached_ms", "median"),
        "dns_popular": _g(r, "dns", "popular_ms", "median"),
        "dl_stability": _g(r, "download", "stability"),
        "ul_stability": _g(r, "upload", "stability"),
        "retrans": _g(r, "download", "server_tcp", "retrans_pct"),
        "dl_errors": _g(r, "download", "errors", default=0),
        "ul_errors": _g(r, "upload", "errors", default=0),
        "ramp": _g(r, "download", "ramp_up_s"),
        "tcp_ms": _g(r, "connection", "tcp_ms"),
        "tls_ms": _g(r, "connection", "tls_ms"),
        "ipv6": _g(r, "connection", "ipv6_available"),
        "colo": _g(r, "meta", "colo"),
        "country": _g(r, "meta", "country") or _g(r, "meta", "loc"),
        "warp": _g(r, "meta", "warp"),
        "lh_multi": _g(r, "localhost", "tcp_multi_stream_mbps"),
        "lh_single": _g(r, "localhost", "tcp_1_stream_mbps"),
        "lh_rtt_us": _g(r, "localhost", "tcp_rtt_us", "median"),
        "lan_capacity": _g(r, "router", "capacity_mbps") if _g(r, "router", "capacity_reliable") else None,
        "lan_large_loss": _g(r, "router", "large_packet_loss_pct"),
        "lan_small_loss": _g(r, "router", "small_packet_loss_pct"),
        "lan_base_rtt": _g(r, "router", "base_rtt_ms"),
    }


# ---------------------------------------------------------------------------- activities
def _worst(*levels):
    order = ["poor", "fair", "good"]
    return min(levels, key=order.index)


def activities(r: dict) -> list[dict]:
    """Rate common uses. status in good / fair / poor, with the limiting factor."""
    k = key_numbers(r)
    down, up = k["down"] or 0, k["up"] or 0
    idle = k["idle_ms"] or 999
    loaded = max(k["dl_loaded"] or idle, k["ul_loaded"] or idle)
    jit = max(k["idle_jitter"] or 0, k["icmp_jitter"] or 0)
    loss = k["loss"] or 0
    bloat = max(k["dl_bloat"] or 0, k["ul_bloat"] or 0)
    out = []

    def add(name, checks):
        """checks: list of (level, reason-if-not-good)."""
        level = _worst(*(c[0] for c in checks))
        reasons = [c[1] for c in checks if c[0] != "good"]
        out.append({"name": name, "status": level,
                    "detail": "; ".join(reasons) if reasons else "All requirements comfortably met"})

    def lv(value, good, fair, higher_better=True):
        if higher_better:
            return "good" if value >= good else "fair" if value >= fair else "poor"
        return "good" if value <= good else "fair" if value <= fair else "poor"

    add("Web browsing & email", [
        (lv(down, 10, 3), f"download {down:.1f} Mbps"),
        (lv(idle, 100, 250, False), f"latency {idle:.0f} ms")])
    add("HD streaming (1080p)", [(lv(down, 10, 5), f"needs ~5–10 Mbps, have {down:.1f}")])
    add("4K streaming", [(lv(down, 40, 25), f"needs ~25 Mbps per stream, have {down:.1f}")])
    add("Video calls (Zoom/Teams/Meet)", [
        (lv(up, 4, 1.5), f"upload {up:.1f} Mbps (HD needs ~3–4)"),
        (lv(down, 6, 2), f"download {down:.1f} Mbps"),
        (lv(loaded, 100, 200, False), f"latency under load {loaded:.0f} ms"),
        (lv(jit, 20, 40, False), f"jitter {jit:.1f} ms"),
        (lv(loss, 0.5, 2, False), f"packet loss {loss:.1f}%")])
    add("Competitive online gaming", [
        (lv(idle, 35, 70, False), f"latency {idle:.0f} ms"),
        (lv(jit, 8, 20, False), f"jitter {jit:.1f} ms"),
        (lv(loss, 0.1, 1, False), f"packet loss {loss:.1f}%"),
        (lv(bloat, 30, 100, False), f"latency jumps +{bloat:.0f} ms under load")])
    add("Cloud gaming (1080p60)", [
        (lv(down, 35, 20), f"download {down:.1f} Mbps"),
        (lv(idle, 30, 60, False), f"latency {idle:.0f} ms"),
        (lv(jit, 5, 15, False), f"jitter {jit:.1f} ms"),
        (lv(loss, 0.1, 1, False), f"packet loss {loss:.1f}%")])
    add("Uploading / cloud backup", [(lv(up, 20, 5), f"upload {up:.1f} Mbps")])
    add("Busy household (4+ users, several 4K streams)", [
        (lv(down, 200, 100), f"download {down:.1f} Mbps"),
        (lv(bloat, 60, 200, False), f"latency jumps +{bloat:.0f} ms when the line is busy")])
    return out


def transfer_times(r: dict) -> list[tuple[str, str]]:
    k = key_numbers(r)
    down, up = k["down"], k["up"]

    def t(size_gb, mbps):
        return human_duration(size_gb * 8000 / mbps) if mbps else "n/a"
    return [
        ("Download a 5 MB photo", t(0.005, down)),
        ("Download a 1 GB file", t(1, down)),
        ("Download a 15 GB 4K movie", t(15, down)),
        ("Download a 100 GB game", t(100, down)),
        ("Upload a 1 GB video", t(1, up)),
        ("Upload 50 GB backup", t(50, up)),
    ]


# ---------------------------------------------------------------------------- layers
def layers(r: dict) -> list[dict]:
    """Performance per layer, from the inside out: this PC, the home network, the internet.

    The slowest layer caps everything above it, which makes the bottleneck easy to spot."""
    k = key_numbers(r)
    lh_icmp = _g(r, "localhost", "icmp_ms", "median")
    out = [{
        "name": "This PC (localhost)",
        "latency_ms": (k["lh_rtt_us"] / 1000) if k["lh_rtt_us"] else lh_icmp,
        "throughput_mbps": k["lh_multi"],
        "detail": (f"TCP loopback {fmt_rate(k['lh_single'])} on 1 stream, {fmt_rate(k['lh_multi'])} on "
                   f"{_g(r, 'localhost', 'streams', default='?')}; round trip {k['lh_rtt_us'] or 0:.0f} µs")
        if k["lh_multi"] else "not measured",
        "method": "TCP loopback, 127.0.0.1",
    }]
    rt = r.get("router") or {}
    cap = rt.get("capacity_mbps")
    out.append({
        "name": "Home network (router hop)",
        "latency_ms": k["gw_ms"] if k["gw_ms"] is not None else k["lan_base_rtt"],
        "throughput_mbps": cap if rt.get("capacity_reliable") else rt.get("large_packet_roundtrip_mbps"),
        "detail": (f"estimated capacity {fmt_rate(cap)} (fit R² {_g(rt, 'fit', 'r2', default=0):.2f}), "
                   f"64 KB echo round trip {fmt_rate(rt.get('large_packet_roundtrip_mbps'))}"
                   + (f", link rate {k['link_mbps']:.0f} Mbps" if k["link_mbps"] else ""))
        if rt.get("sizes") else (rt.get("error") or "not measured"),
        "method": "ICMP size sweep" + ("" if rt.get("capacity_reliable") else " (low confidence)"),
    })
    out.append({
        "name": "Internet (Cloudflare " + str(k["colo"] or "?") + ")",
        "latency_ms": k["idle_ms"],
        "throughput_mbps": k["down"],
        "detail": f"download {fmt_rate(k['down'])}, upload {fmt_rate(k['up'])}",
        "method": "HTTPS multi-stream",
    })
    return out


def fmt_rate(mbps) -> str:
    if mbps is None:
        return "n/a"
    if mbps >= 1000:
        return f"{mbps / 1000:.1f} Gbps"
    return f"{mbps:.0f} Mbps" if mbps >= 10 else f"{mbps:.1f} Mbps"


# ---------------------------------------------------------------------------- issues
def _issue(sev, title, detail, fixes=None, area="general"):
    return {"severity": sev, "title": title, "detail": detail, "fixes": fixes or [], "area": area}


def diagnose(r: dict) -> list[dict]:
    k = key_numbers(r)
    issues: list[dict] = []
    wifi = k["medium"] == "wifi"
    down, up = k["down"], k["up"]

    # ---------------- local link (Wi-Fi / Ethernet)
    if wifi and k["rssi"] is not None:
        if k["rssi"] <= -80:
            issues.append(_issue("critical", f"Very weak Wi-Fi signal ({k['rssi']} dBm)",
                "Below about −80 dBm the radio drops to slow modulation rates and retransmits often. "
                "That cuts throughput and adds jitter and loss, whatever your internet plan is.",
                ["Move closer to the router or remove obstacles (walls, metal, aquariums, microwaves).",
                 "Put the router up high, in the middle of the home, out of cabinets.",
                 "Add a mesh node or wired access point near where you work.",
                 "Use an Ethernet cable (or a powerline adapter) for desks, consoles and TVs."], "local"))
        elif k["rssi"] <= -70:
            issues.append(_issue("warning", f"Weak Wi-Fi signal ({k['rssi']} dBm)",
                "Signal is fair-to-weak (good is better than about −65 dBm). Expect lower speeds "
                "and more latency spikes than your connection can deliver.",
                ["Move closer to the router, or the router closer to you.",
                 "Consider a mesh system or wired access point for this room.",
                 "Use Ethernet where possible."], "local"))
        elif k["rssi"] <= -67:
            issues.append(_issue("info", f"Fair Wi-Fi signal ({k['rssi']} dBm)",
                "Usable, but at the edge of what real-time apps like calls and gaming tolerate.", [], "local"))
    elif wifi and k["signal_pct"] is not None and k["signal_pct"] < 50:
        issues.append(_issue("warning", f"Weak Wi-Fi signal ({k['signal_pct']}%)",
            "Low signal quality reduces throughput and adds latency spikes.",
            ["Move closer to the router or use Ethernet."], "local"))

    if wifi and k["band"] and k["band"].startswith("2.4"):
        issues.append(_issue("info", "Connected on the 2.4 GHz band",
            "2.4 GHz reaches further but is slow and crowded: neighbours, Bluetooth and microwaves all share it.",
            ["Connect to the 5 GHz (or 6 GHz) network if the router has one; give it a separate SSID if band steering misbehaves.",
             "On 2.4 GHz, use channels 1, 6 or 11 at 20 MHz width."], "local"))

    if k["link_mbps"] and down:
        lm = k["link_mbps"]
        if wifi and down >= 0.55 * lm and not (k["lan_capacity"] and down >= 0.7 * k["lan_capacity"]):
            issues.append(_issue("warning", "Your Wi-Fi link is likely the bottleneck",
                f"Wi-Fi negotiated {lm:.0f} Mbps (PHY rate). Real TCP throughput is usually 50–70% of that, "
                f"and you measured {down:.0f} Mbps. Your internet plan may be faster than the Wi-Fi can carry.",
                ["Improve the signal (closer, fewer walls) to raise the link rate.",
                 "Use a 5/6 GHz band with 80/160 MHz channels and a Wi-Fi 6/6E/7 client.",
                 "Use Ethernet to see the true line speed."], "local"))
        elif not wifi and lm <= 100 and down >= 0.8 * lm:
            issues.append(_issue("warning", f"Ethernet link negotiated at only {lm:.0f} Mbps",
                "The port or cable is running at Fast Ethernet speed, which caps you near 94 Mbps.",
                ["Swap the cable for a Cat5e/Cat6 one. A broken pair forces 100 Mbps.",
                 "Try a different port on the router or switch; check the NIC driver/settings."], "local"))
        elif down < 0.25 * lm and (k["dl_stability"] or 0) > 75:
            issues.append(_issue("info", "Speed is limited upstream, not by your local link",
                f"Local link {lm:.0f} Mbps vs {down:.0f} Mbps measured, with steady throughput. "
                "This usually means your ISP plan (or the ISP's network) sets the cap.",
                ["Compare with the speed your contract advertises. If it's well below, contact the ISP with these results."],
                "isp"))

    # ---------------- this PC / home network capacity (layered tests)
    if k["lh_multi"] is not None:
        if k["lh_multi"] < 1000:
            issues.append(_issue("warning", f"This PC's network stack is slow ({fmt_rate(k['lh_multi'])} on localhost)",
                "Even traffic that never leaves the PC moves slowly. A modern PC does 10+ Gbps over loopback. "
                "Security software, VPN/filter drivers, a busy CPU or an aggressive power-saving plan usually cause this.",
                ["Switch Windows to the 'Balanced' or 'Best performance' power mode and plug in the laptop.",
                 "Check Task Manager for CPU hogs during the test.",
                 "Temporarily disable antivirus web protection or VPN clients and retest."], "pc"))
        elif down and k["lh_multi"] < 5 * down:
            issues.append(_issue("info", "This PC is close to its own network limit",
                f"Localhost throughput {fmt_rate(k['lh_multi'])} is not far above your internet speed, "
                "so the PC itself may cap results.", ["Retest with fewer background apps running."], "pc"))
    if k["lh_rtt_us"] is not None and k["lh_rtt_us"] > 150:
        issues.append(_issue("info", f"Slow local packet processing ({k['lh_rtt_us']:.0f} µs loopback round trip)",
            "Loopback round trips are normally 10–60 µs. Higher values point to CPU power saving (deep C-states) "
            "or heavy background load. That adds a little latency to every packet.",
            ["Use the 'Best performance' power mode while gaming or on calls."], "pc"))

    cap = k["lan_capacity"]
    if cap and down:
        if down >= 0.7 * cap:
            issues.append(_issue("warning", f"Your home network is the bottleneck (≈{fmt_rate(cap)} to the router)",
                f"The router hop carries about {fmt_rate(cap)}, and the internet test reached {fmt_rate(down)}. "
                "Your internet plan is probably faster than your "
                + ("Wi-Fi" if wifi else "LAN") + " can deliver.",
                (["Get closer to the router or improve the signal; a stronger signal raises the link rate.",
                  "Use 5/6 GHz with wide (80/160 MHz) channels.",
                  "Use Ethernet for the devices that need full speed."] if wifi else
                 ["Check cables and switch ports for gigabit link; replace old cables/switches."]), "local"))
        elif cap >= 3 * down:
            issues.append(_issue("info", f"Home network has headroom ({fmt_rate(cap)} to the router)",
                f"The hop to your router carries several times your internet speed ({fmt_rate(down)}), "
                "so the local network isn't what limits throughput.", [], "isp"))
    if (k["lan_large_loss"] or 0) >= 10 and not (k["lan_small_loss"] or 0):
        issues.append(_issue("warning", f"Large packets get lost on the way to the router ({k['lan_large_loss']:.0f}%)",
            "Small pings arrive but large (fragmented) ones are dropped. On Wi-Fi that usually means interference or "
            "a marginal signal: any lost fragment loses the whole packet.",
            ["Improve the Wi-Fi signal or change the channel.",
             "If you're wired, check the cable and the switch."], "local"))

    # ---------------- router (gateway) health
    gw = k["gw_ms"]
    if gw is not None:
        limit = 10 if wifi else 3
        if gw > limit * 3:
            issues.append(_issue("warning", f"High latency to your own router ({gw:.1f} ms)",
                f"A healthy {'Wi-Fi' if wifi else 'wired'} hop to the router is under ~{limit} ms. "
                "This delay happens inside your home network, before reaching the ISP.",
                (["Improve Wi-Fi signal or switch to a less congested channel/band.",
                  "Check for other devices saturating the Wi-Fi (backups, downloads, cameras)."] if wifi else
                 ["Restart the router; check for an overloaded router or switch."]) + [
                 "Disable 'power saving' for the Wi-Fi adapter (Device Manager → adapter → Power Management/Advanced)."],
                "local"))
    if k["gw_loss"]:
        sev = "critical" if k["gw_loss"] >= 3 else "warning"
        issues.append(_issue(sev, f"Packet loss inside your home network ({k['gw_loss']:.1f}% to router)",
            "Packets are lost before they even reach the router, which points to Wi-Fi interference/weak "
            "signal or a faulty cable or port.",
            ["Check signal strength and move closer, or use Ethernet.",
             "Change the Wi-Fi channel. Neighbouring networks on the same channel cause collisions.",
             "Replace the cable or try another port if you are wired."], "local"))
    if k["gw_p95"] and gw and k["gw_p95"] > max(20, 4 * gw):
        issues.append(_issue("info", "Latency spikes on the Wi-Fi/LAN hop",
            f"Router latency is {gw:.1f} ms typically but {k['gw_p95']:.0f} ms at the 95th percentile. "
            "Spikes like this come from Wi-Fi contention, background scans or power saving.",
            ["Set the adapter's 'Roaming aggressiveness' lower and turn off power saving.",
             "Close apps that scan for networks (location services can trigger Wi-Fi scans)."], "local"))

    # ---------------- bufferbloat
    for direction, bloat, gw_loaded in (("download", k["dl_bloat"], k["dl_gw_loaded"]),
                                        ("upload", k["ul_bloat"], k["ul_gw_loaded"])):
        if bloat is None or bloat < 30:
            continue
        sev = "critical" if bloat >= 200 else "warning" if bloat >= 60 else "info"
        local_part = (gw_loaded - gw) if (gw_loaded is not None and gw is not None) else None
        where = ""
        if local_part is not None:
            if local_part > 0.5 * bloat:
                where = (f" Most of it (+{local_part:.0f} ms) already shows on the hop to your router, "
                         "so the queue is in the Wi-Fi/router.")
            else:
                where = (" The hop to your router stays fast, so the queue builds at the modem or the ISP's "
                         "uplink/downlink.")
        issues.append(_issue(sev, f"Bufferbloat on {direction}: +{bloat:.0f} ms under load",
            f"When the line is busy, latency rises from idle to {bloat:.0f} ms more. Calls stutter and games lag "
            f"whenever someone downloads or uploads.{where}",
            ["Enable Smart Queue Management (SQM / fq_codel / CAKE) on the router. On consumer routers it may be "
             "called 'QoS', 'Adaptive QoS', or 'Airtime fairness'.",
             f"Set the SQM/QoS {direction} limit to about 90% of the measured "
             f"{(down if direction == 'download' else up) or 0:.0f} Mbps.",
             "If the ISP router can't do it, put an SQM-capable router behind it (OpenWrt, Ubiquiti, Eero, "
             "some ASUS/TP-Link models)."], "router"))

    # ---------------- internet path
    loss = k["loss"]
    if loss is not None and loss > 0:
        if k["gw_loss"]:
            pass  # already reported as local
        else:
            sev = "critical" if loss >= 2 else "warning" if loss >= 0.5 else "info"
            issues.append(_issue(sev, f"Packet loss on the internet path ({loss:.1f}%)",
                "The router hop shows no loss, so packets are being dropped past it: at the modem or line, "
                "or inside the ISP's network. Loss hurts calls and gaming more than speed does.",
                ["Restart the modem/ONT; check the DSL/cable signal levels in its status page.",
                 "Check the coax/phone/fiber connectors and splitters.",
                 "If it persists at different times of day, report it to the ISP with these results."], "isp"))

    idle = k["idle_ms"]
    if idle is not None and idle > 80:
        issues.append(_issue("warning" if idle > 150 else "info", f"High base latency ({idle:.0f} ms)",
            f"Round-trip to the nearest Cloudflare server ({k['colo'] or '?'}) is high. A typical fixed "
            "line is 5–30 ms; satellite, mobile networks or VPNs add more.",
            ["Disable VPNs and proxies, or pick a nearer VPN exit.",
             "If you're on mobile/4G, place the router near a window.",
             "Ask the ISP about routing if the server location looks far from you."], "isp"))

    jit = k["icmp_jitter"] if k["icmp_jitter"] is not None else k["idle_jitter"]
    if jit is not None and jit > 15:
        issues.append(_issue("warning" if jit > 30 else "info", f"High jitter ({jit:.1f} ms)",
            "Latency varies a lot from packet to packet, so real-time apps need larger buffers (they add delay).",
            ["If you're on Wi-Fi, try Ethernet to see whether the jitter disappears (then the problem is local).",
             "Pause other heavy traffic on the network and retest."], "local" if wifi else "isp"))

    # ---------------- throughput quality
    for direction, stab in (("download", k["dl_stability"]), ("upload", k["ul_stability"])):
        if stab is not None and stab < 65:
            issues.append(_issue("warning" if stab < 45 else "info",
                f"Unstable {direction} throughput (stability {stab:.0f}/100)",
                "Speed swings widely during the test. Common causes: Wi-Fi interference, other devices "
                "using bandwidth, or ISP congestion.",
                ["Retest with other devices idle, and over Ethernet, to isolate the cause.",
                 "Run tests at different times. Evening-only drops point to ISP congestion."],
                "local" if wifi else "isp"))
    if k["retrans"] is not None and k["retrans"] > 2:
        issues.append(_issue("warning" if k["retrans"] > 5 else "info",
            f"TCP retransmissions on download ({k['retrans']:.1f}% of packets)",
            "Cloudflare's server had to resend a noticeable share of packets, a sign of loss or "
            "congestion somewhere on the path.",
            ["Check for packet loss to your router (above) to see if it is local.",
             "Persistent high retransmissions on a wired connection are worth reporting to the ISP."], "isp"))
    if (k["dl_errors"] or 0) + (k["ul_errors"] or 0) > 3:
        issues.append(_issue("warning", "Connections dropped during the test",
            f"{k['dl_errors']} download / {k['ul_errors']} upload stream errors. Connections are being reset "
            "or timing out.",
            ["Check firewall/antivirus web shields, which may throttle or inspect traffic.",
             "Restart the router/modem and retest."], "general"))
    if k["ramp"] is not None and k["ramp"] > 4:
        issues.append(_issue("info", f"Slow ramp-up ({k['ramp']:.1f} s to reach full speed)",
            "TCP took a while to fill the pipe. This happens with high latency or loss early in the "
            "transfer, and makes short downloads and web pages feel slower than the headline speed.",
            [], "isp"))

    # ---------------- DNS
    if k["dns_uncached"] is not None and k["dns_uncached"] > 150:
        issues.append(_issue("warning" if k["dns_uncached"] > 300 else "info",
            f"Slow DNS resolver ({k['dns_uncached']:.0f} ms for a fresh lookup)",
            "Every new website needs DNS lookups first; a slow resolver delays page loads even on a fast line.",
            ["Switch DNS to a fast public resolver: 1.1.1.1 / 1.0.0.1 (Cloudflare), 8.8.8.8 (Google) or 9.9.9.9 (Quad9). "
             "Set it on the router for all devices, or in Windows Settings → Network → DNS.",
             "Enable DNS-over-HTTPS in Windows/browser for privacy as well."], "isp"))
    elif k["dns_popular"] is not None and k["dns_popular"] > 40:
        issues.append(_issue("info", f"DNS cache seems ineffective ({k['dns_popular']:.0f} ms for popular sites)",
            "Popular names are normally answered from a cache in a few ms.",
            ["Use the router's DNS cache or a public resolver; check that no VPN/security app forces remote DNS."],
            "isp"))

    # ---------------- connection setup / misc
    if k["tcp_ms"] and k["tls_ms"] and k["tls_ms"] > 2.5 * k["tcp_ms"] + 25:
        issues.append(_issue("info", "TLS handshake slower than expected",
            f"TCP connect {k['tcp_ms']:.0f} ms but TLS {k['tls_ms']:.0f} ms. Antivirus 'HTTPS scanning', "
            "corporate proxies or a slow CPU can cause this.",
            ["If you use antivirus with web/HTTPS scanning, try disabling that feature and retest."], "general"))
    if k["ipv6"] is False:
        issues.append(_issue("info", "No IPv6 connectivity",
            "Your connection uses IPv4 only. That's fine today, though IPv6 can avoid carrier-grade NAT issues "
            "for gaming and peer-to-peer.",
            ["Check whether your ISP offers IPv6 and enable it on the router."], "isp"))
    if down and up and down / up > 10:
        issues.append(_issue("info", f"Very asymmetric line ({down / up:.0f}:1 down/up)",
            "Upload is the limit for video calls with several people, cloud backup and streaming.",
            ["If upload matters, look at fiber (symmetric) plans."], "isp"))
    if k["warp"] and k["warp"] != "off":
        issues.append(_issue("info", "Cloudflare WARP/VPN detected",
            "Results reflect the VPN tunnel, not your raw ISP connection.", [], "general"))

    if not any(i["severity"] in ("critical", "warning") for i in issues):
        issues.append(_issue("good", "No significant problems detected",
            "Latency, loss, stability and bufferbloat are all within healthy ranges.", [], "general"))
    issues.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
    return issues


def bottleneck_summary(r: dict) -> str:
    """One-sentence verdict on where problems are coming from."""
    issues = diagnose(r)
    bad = [i for i in issues if i["severity"] in ("critical", "warning")]
    if not bad:
        return "Your connection looks healthy end-to-end."
    areas = {}
    for i in bad:
        areas[i["area"]] = areas.get(i["area"], 0) + (2 if i["severity"] == "critical" else 1)
    top = max(areas, key=areas.get)
    return {
        "pc": "The main limit is this PC itself (slow local network stack). Check power settings and security software.",
        "local": "Most problems are inside your home network (Wi-Fi/LAN). Fix those first; they're free to fix.",
        "router": "The main problem is queueing in the router or modem (bufferbloat). Router settings can fix it.",
        "isp": "Your home network looks fine. The remaining problems sit on the ISP side of the router.",
        "general": "Some issues were detected; see the recommendations below.",
    }[top]
