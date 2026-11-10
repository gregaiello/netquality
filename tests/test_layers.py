from netquality import diagnosis, stats
from netquality.local import measure_localhost

from test_stats_diag import _result


def test_linear_fit_recovers_capacity():
    # 100 Mbps symmetric link: each payload byte adds 16 bits / 100e6 bps = 0.00016 ms
    pts = [(s, 1.0 + s * 0.00016) for s in (32, 8000, 32000, 65000)]
    fit = stats.linear_fit(pts)
    assert abs(fit["r2"] - 1) < 1e-9
    assert abs(0.016 / fit["slope"] - 100) < 1e-6
    assert stats.linear_fit([(1, 1), (2, 2)]) is None


def _with_layers(cap=150.0, lh=20000.0, down=120.0, large_loss=0.0):
    r = _result()
    r["download"]["mbps"] = down
    r["localhost"] = {"tcp_multi_stream_mbps": lh, "tcp_1_stream_mbps": lh / 3, "streams": 4,
                      "tcp_rtt_us": {"median": 20}}
    r["router"] = {"capacity_mbps": cap, "capacity_reliable": True, "fit": {"r2": 0.99},
                   "base_rtt_ms": 1.5, "large_packet_roundtrip_mbps": 110, "sizes": [{"size": 32}],
                   "large_packet_loss_pct": large_loss, "small_packet_loss_pct": 0.0}
    return r


def test_home_network_bottleneck_detected_and_supersedes_phy_rule():
    titles = [i["title"] for i in diagnosis.diagnose(_with_layers(cap=150, down=120))]
    assert any("home network is the bottleneck" in t for t in titles)
    assert not any("Wi-Fi link is likely the bottleneck" in t for t in titles)


def test_home_network_headroom_and_slow_pc():
    r = _with_layers(cap=900, down=50, lh=600)
    issues = diagnosis.diagnose(r)
    titles = [i["title"] for i in issues]
    assert any("headroom" in t for t in titles)
    pc = next(i for i in issues if "network stack is slow" in i["title"])
    assert pc["severity"] == "warning" and pc["area"] == "pc"


def test_large_packet_loss_rule():
    titles = [i["title"] for i in diagnosis.diagnose(_with_layers(large_loss=20))]
    assert any("Large packets get lost" in t for t in titles)


def test_layers_order_and_values():
    ls = diagnosis.layers(_with_layers())
    assert [x["name"].split(" ")[0] for x in ls] == ["This", "Home", "Internet"]
    assert ls[0]["throughput_mbps"] == 20000 and ls[1]["throughput_mbps"] == 150
    assert diagnosis.fmt_rate(20000) == "20.0 Gbps" and diagnosis.fmt_rate(5.25) == "5.2 Mbps"


def test_localhost_measurement_runs():
    res = measure_localhost(streams=2, seconds=0.3)
    assert res["tcp_1_stream_mbps"] > 100 and res["tcp_multi_stream_mbps"] > 100
    assert res["tcp_rtt_us"]["count"] == 2000
