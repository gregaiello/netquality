"""Pure statistics helpers (no I/O, no Qt) so they are easy to test."""
from __future__ import annotations

import math
import statistics
from typing import Iterable, Sequence


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolated percentile, p in [0, 100]."""
    data = sorted(v for v in values if v is not None and not math.isnan(v))
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return data[int(k)]
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def jitter(rtts: Sequence[float]) -> float | None:
    """Mean absolute difference between consecutive RTT samples (RFC 3550 style)."""
    clean = [r for r in rtts if r is not None]
    if len(clean) < 2:
        return None
    diffs = [abs(b - a) for a, b in zip(clean, clean[1:])]
    return sum(diffs) / len(diffs)


def summarize(values: Iterable[float]) -> dict | None:
    data = [v for v in values if v is not None]
    if not data:
        return None
    return {
        "count": len(data),
        "min": min(data),
        "max": max(data),
        "mean": statistics.fmean(data),
        "median": percentile(data, 50),
        "p10": percentile(data, 10),
        "p90": percentile(data, 90),
        "p95": percentile(data, 95),
        "stdev": statistics.pstdev(data) if len(data) > 1 else 0.0,
    }


def windowed_rates(samples: Sequence[tuple[float, int]], window: float) -> list[tuple[float, float]]:
    """Turn cumulative (t_seconds, total_bytes) samples into Mbps over fixed windows.

    Returns a list of (window_end_t, mbps).
    """
    if len(samples) < 2:
        return []
    out = []
    start_t, start_b = samples[0]
    for t, b in samples[1:]:
        if t - start_t >= window - 1e-9:
            out.append((t, (b - start_b) * 8 / (t - start_t) / 1e6))
            start_t, start_b = t, b
    return out


def stability_score(rates: Sequence[float]) -> float | None:
    """0..100, 100 = perfectly flat throughput. Based on coefficient of variation."""
    if len(rates) < 2:
        return None
    mean = statistics.fmean(rates)
    if mean <= 0:
        return 0.0
    cov = statistics.pstdev(rates) / mean
    return max(0.0, min(100.0, 100.0 * (1.0 - cov)))


def linear_fit(points: Sequence[tuple[float, float]]) -> dict | None:
    """Least-squares y = slope*x + intercept, with coefficient of determination r2."""
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for _, y in points)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"slope": slope, "intercept": intercept, "r2": r2}


BLOAT_GRADES =[(5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")]


def bufferbloat_grade(increase_ms: float | None) -> str:
    """Grade the latency increase under load (same scale popularised by Waveform)."""
    if increase_ms is None:
        return "?"
    for limit, grade in BLOAT_GRADES:
        if increase_ms < limit:
            return grade
    return "F"


def parse_server_timing(header: str | None) -> dict:
    """Parse a Server-Timing header into {name: {"dur": float, "desc": str, ...}}."""
    out: dict = {}
    if not header:
        return out
    for metric in header.split(","):
        parts = [p.strip() for p in metric.split(";") if p.strip()]
        if not parts:
            continue
        name = parts[0]
        entry: dict = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                v = v.strip().strip('"')
                if k == "dur":
                    try:
                        entry["dur"] = float(v)
                    except ValueError:
                        pass
                else:
                    entry[k] = v
        out[name] = entry
    return out


def parse_cf_l4(desc: str | None) -> dict:
    """Parse Cloudflare's cfL4 desc ("?proto=TCP&rtt=62488&...") into numbers.

    rtt / min_rtt / rtt_var are microseconds; sent/recv/lost/retrans are packet counts.
    """
    out: dict = {}
    if not desc:
        return out
    for pair in desc.lstrip("?").split("&"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        try:
            out[k] = float(v) if "." in v else int(v)
        except ValueError:
            out[k] = v
    return out


def human_duration(seconds: float | None) -> str:
    if seconds is None or math.isinf(seconds) or math.isnan(seconds):
        return "n/a"
    if seconds < 1:
        return "< 1 s"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"
