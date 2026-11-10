from netquality import diagnosis, stats
from netquality.sysinfo import parse_netsh_wlan


def test_percentile_and_jitter():
    assert stats.percentile([1, 2, 3, 4], 50) == 2.5
    assert stats.percentile([5], 90) == 5
    assert stats.percentile([], 50) is None
    assert stats.jitter([10, 12, 10, 14]) == (2 + 2 + 4) / 3


def test_windowed_rates_and_stability():
    cum = [(0.0, 0), (0.5, 625_000), (1.0, 1_250_000)]  # 10 Mbps
    rates = stats.windowed_rates(cum, 0.5)
    assert [round(m, 6) for _, m in rates] == [10.0, 10.0]
    assert stats.stability_score([10, 10, 10]) == 100.0
    assert stats.stability_score([0, 20]) == 0.0


def test_bufferbloat_grade():
    assert stats.bufferbloat_grade(2) == "A+"
    assert stats.bufferbloat_grade(45) == "B"
    assert stats.bufferbloat_grade(500) == "F"
    assert stats.bufferbloat_grade(None) == "?"


def test_server_timing_cf_l4():
    st = stats.parse_server_timing('cfL4;desc="?proto=TCP&rtt=62488&min_rtt=58147&sent=4&retrans=0"')
    l4 = stats.parse_cf_l4(st["cfL4"]["desc"])
    assert l4["rtt"] == 62488 and l4["sent"] == 4 and l4["proto"] == "TCP"
    assert stats.parse_server_timing("cfSpeedEdge;dur=4, cfSpeedWorker;dur=18")["cfSpeedWorker"]["dur"] == 18.0


NETSH = """
    Name                   : WiFi
    SSID                   : HomeNet
    Band                   : 5 GHz
    Channel                : 40
    Radio type             : 802.11ac
    Receive rate (Mbps)    : 195
    Transmit rate (Mbps)   : 180
    Signal                 : 46%
    Rssi                   : -78
"""
NETSH_IT = """
    Nome                   : Wi-Fi
    SSID                   : Casa
    Banda                  : 2,4 GHz
    Canale                 : 6
    Tipo frequenza radio   : 802.11n
    Velocità ricezione (Mbps)   : 72
    Velocità trasmissione (Mbps) : 65
    Segnale                : 81%
"""


def test_parse_netsh():
    w = parse_netsh_wlan(NETSH)
    assert w == {"ssid": "HomeNet", "band": "5 GHz", "channel": 40, "radio": "802.11ac",
                 "rx_rate_mbps": 195.0, "tx_rate_mbps": 180.0, "signal_pct": 46, "rssi_dbm": -78}
    it = parse_netsh_wlan(NETSH_IT)
    assert it["band"] == "2,4 GHz" and it["channel"] == 6 and it["signal_pct"] == 81 and it["rx_rate_mbps"] == 72


def _result(**over):
    r = {
        "link": {"medium": "wifi", "link_mbps": 195, "gateway": "192.168.1.1",
                 "wifi": {"rssi_dbm": -78, "band": "5 GHz", "signal_pct": 46}},
        "connection": {"tcp_ms": 20, "tls_ms": 25, "ipv6_available": True},
        "meta": {"colo": "ZRH"},
        "latency": {"http": {"median": 20, "jitter_ms": 2},
                    "icmp": {"loss_pct": 0, "jitter_ms": 2, "summary": {"median": 18}},
                    "gateway": {"loss_pct": 0, "jitter_ms": 1, "summary": {"median": 3, "p95": 5}}},
        "dns": {"uncached_ms": {"median": 40}, "popular_ms": {"median": 2}},
        "download": {"mbps": 120, "stability": 90, "bufferbloat_ms": 150, "errors": 0,
                     "loaded_latency": {"median": 170},
                     "gateway_loaded": {"summary": {"median": 4}}},
        "upload": {"mbps": 40, "stability": 90, "bufferbloat_ms": 10, "errors": 0,
                   "loaded_latency": {"median": 30}},
    }
    r.update(over)
    return r


def test_diagnose_flags_wifi_bloat_and_location():
    issues = diagnosis.diagnose(_result())
    titles = " | ".join(i["title"] for i in issues)
    assert "Weak Wi-Fi signal" in titles
    assert "Wi-Fi link is likely the bottleneck" in titles       # 120 >= 0.55*195
    bloat = next(i for i in issues if i["title"].startswith("Bufferbloat on download"))
    assert bloat["severity"] == "warning" and "modem or the ISP" in bloat["detail"]
    assert all(i["fixes"] for i in issues if i["severity"] in ("critical", "warning"))
    assert issues[0]["severity"] in ("critical", "warning")       # sorted by severity


def test_diagnose_healthy_and_internet_loss():
    healthy = _result(link={"medium": "ethernet", "link_mbps": 1000, "gateway": None, "wifi": None})
    healthy["download"]["bufferbloat_ms"] = 3
    assert diagnosis.diagnose(healthy)[-1]["severity"] in ("good", "info")
    assert not [i for i in diagnosis.diagnose(healthy) if i["severity"] in ("critical", "warning")]
    lossy = _result()
    lossy["latency"]["icmp"]["loss_pct"] = 3.0
    t = [i for i in diagnosis.diagnose(lossy) if "internet path" in i["title"]]
    assert t and t[0]["severity"] == "critical" and t[0]["area"] == "isp"


def test_activities_and_times():
    acts = {a["name"]: a for a in diagnosis.activities(_result())}
    assert acts["4K streaming"]["status"] == "good"
    assert acts["Competitive online gaming"]["status"] == "poor"   # +150 ms bloat
    times = dict(diagnosis.transfer_times(_result()))
    assert times["Download a 1 GB file"] == "67 s"
