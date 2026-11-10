"""Custom painted widgets: speed gauge, line chart, latency box plot, metric card."""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

BG = QColor("#12151c")
PANEL = QColor("#1a1e27")
GRID = QColor("#2a303c")
TEXT = QColor("#e6e9ef")
MUTED = QColor("#8a93a6")
DL = QColor("#22d3ee")      # cyan
UL = QColor("#c084fc")      # violet
IDLE = QColor("#4ade80")    # green
WARN = QColor("#fbbf24")
BAD = QColor("#f87171")


def nice_ceiling(v: float) -> float:
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


class Gauge(QWidget):
    """Speedometer with a non-linear (speedtest-like) scale."""
    TICKS = [0, 5, 10, 25, 50, 100, 250, 500, 1000]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.unit = "Mbps"
        self.caption = "Ready"
        self.color = DL
        self.setMinimumSize(280, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_value(self, v: float, caption: str | None = None, color: QColor | None = None, unit: str | None = None):
        self.value = max(0.0, v)
        if caption is not None:
            self.caption = caption
        if color is not None:
            self.color = color
        if unit is not None:
            self.unit = unit
        self.update()

    def _frac(self, v: float) -> float:
        t = self.TICKS
        if v >= t[-1]:
            return 1.0
        for i in range(len(t) - 1):
            if t[i] <= v < t[i + 1]:
                return (i + (v - t[i]) / (t[i + 1] - t[i])) / (len(t) - 1)
        return 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height() * 1.15)
        r = side * 0.42
        c = QPointF(self.width() / 2, self.height() * 0.55)
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        start, span = 225.0, -270.0
        pen = QPen(GRID, r * 0.12, cap=Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, int(start * 16), int(span * 16))
        frac = self._frac(self.value) if self.unit == "Mbps" else min(1.0, self.value / 200)
        pen.setColor(self.color)
        p.setPen(pen)
        if frac > 0:
            p.drawArc(rect, int(start * 16), int(span * frac * 16))
        # tick labels
        p.setPen(MUTED)
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, r * 0.065))
        p.setFont(f)
        if self.unit == "Mbps":
            for i, t in enumerate(self.TICKS):
                a = math.radians(start + span * i / (len(self.TICKS) - 1))
                pt = QPointF(c.x() + math.cos(a) * r * 0.74, c.y() - math.sin(a) * r * 0.74)
                p.drawText(QRectF(pt.x() - 22, pt.y() - 9, 44, 18), Qt.AlignmentFlag.AlignCenter, str(t))
        # needle
        a = math.radians(start + span * frac)
        p.setPen(QPen(TEXT, max(2.0, r * 0.02), cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(c.x() + math.cos(a) * r * 0.2, c.y() - math.sin(a) * r * 0.2),
                   QPointF(c.x() + math.cos(a) * r * 0.58, c.y() - math.sin(a) * r * 0.58))
        # value text
        f.setPointSizeF(max(14.0, r * 0.24))
        f.setBold(True)
        p.setFont(f)
        p.setPen(TEXT)
        txt = f"{self.value:.1f}" if self.value < 100 else f"{self.value:.0f}"
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.15, 2 * r, r * 0.35), Qt.AlignmentFlag.AlignCenter, txt)
        f.setPointSizeF(max(8.0, r * 0.08))
        f.setBold(False)
        p.setFont(f)
        p.setPen(MUTED)
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.5, 2 * r, r * 0.2), Qt.AlignmentFlag.AlignCenter, self.unit)
        p.setPen(self.color)
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.72, 2 * r, r * 0.22), Qt.AlignmentFlag.AlignCenter, self.caption)


class LineChart(QWidget):
    """Multi-series XY chart. series: name -> (color, [(x, y), ...])."""

    def __init__(self, title: str, x_label: str = "s", y_label: str = "", parent=None):
        super().__init__(parent)
        self.title, self.x_label, self.y_label = title, x_label, y_label
        self.series: dict[str, tuple[QColor, list[tuple[float, float]]]] = {}
        self.x_ticks: list[str] | None = None  # optional categorical labels
        self.setMinimumHeight(180)

    def clear(self):
        self.series.clear()
        self.update()

    def set_series(self, name: str, color: QColor, points: list[tuple[float, float]]):
        self.series[name] = (color, list(points))
        self.update()

    def append(self, name: str, color: QColor, x: float, y: float):
        self.series.setdefault(name, (color, []))[1].append((x, y))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), PANEL)
        L, R, T, B = 52, 14, 42, 26
        w, h = self.width() - L - R, self.height() - T - B
        p.setPen(TEXT)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(10, 4, self.width() - 20, 20), Qt.AlignmentFlag.AlignLeft, self.title)
        f.setBold(False)
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1))
        p.setFont(f)
        pts = [pt for _, (_, s) in self.series.items() for pt in s if pt[1] is not None]
        if not pts or w <= 10 or h <= 10:
            p.setPen(MUTED)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data yet")
            return
        xmin = min(x for x, _ in pts)
        xmax = max(x for x, _ in pts)
        if xmax - xmin < 1e-9:
            xmax = xmin + 1
        ymax = nice_ceiling(max(y for _, y in pts) * 1.1)

        def map_pt(x, y):
            return QPointF(L + (x - xmin) / (xmax - xmin) * w, T + h - min(y, ymax) / ymax * h)

        for i in range(5):
            y = T + h * i / 4
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(L, y), QPointF(L + w, y))
            p.setPen(MUTED)
            v = ymax * (4 - i) / 4
            p.drawText(QRectF(0, y - 8, L - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{v:.0f}" if v >= 10 else f"{v:.1f}")
        p.setPen(MUTED)
        for i in range(6):
            x = xmin + (xmax - xmin) * i / 5
            px = L + w * i / 5
            label = self.x_ticks[min(len(self.x_ticks) - 1, round(x))] if self.x_ticks else f"{x:.0f}{self.x_label}"
            p.drawText(QRectF(px - 45, T + h + 4, 90, 16), Qt.AlignmentFlag.AlignCenter, label)
        if self.y_label:
            p.drawText(QRectF(4, T - 16, L, 14), Qt.AlignmentFlag.AlignLeft, self.y_label)

        lx = L + w
        for name, (color, s) in reversed(list(self.series.items())):
            s = [pt for pt in s if pt[1] is not None]
            if s:
                path = QPainterPath(map_pt(*s[0]))
                for pt in s[1:]:
                    path.lineTo(map_pt(*pt))
                p.setPen(QPen(color, 2))
                p.drawPath(path)
                if len(s) == 1:
                    p.setBrush(color)
                    p.drawEllipse(map_pt(*s[0]), 3, 3)
                    p.setBrush(Qt.BrushStyle.NoBrush)
            tw = p.fontMetrics().horizontalAdvance(name) + 22
            lx -= tw
            p.fillRect(QRectF(lx, 9, 10, 10), color)
            p.setPen(MUTED)
            p.drawText(QRectF(lx + 13, 4, tw, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)


class BoxPlot(QWidget):
    """Horizontal box plots of latency distributions: rows of (label, color, summary dict)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.rows: list[tuple[str, QColor, dict]] = []
        self.setMinimumHeight(170)

    def set_rows(self, rows):
        self.rows = [r for r in rows if r[2]]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), PANEL)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(TEXT)
        p.drawText(QRectF(10, 4, self.width() - 20, 20), Qt.AlignmentFlag.AlignLeft, self.title)
        f.setBold(False)
        p.setFont(f)
        if not self.rows:
            p.setPen(MUTED)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data yet")
            return
        L, R, T, B = 150, 20, 30, 24
        w = self.width() - L - R
        h = self.height() - T - B
        vmax = nice_ceiling(max(r[2]["max"] for r in self.rows) * 1.05)
        X = lambda v: L + min(v, vmax) / vmax * w  # noqa: E731
        for i in range(6):
            x = L + w * i / 5
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(x, T), QPointF(x, T + h))
            p.setPen(MUTED)
            p.drawText(QRectF(x - 30, T + h + 4, 60, 16), Qt.AlignmentFlag.AlignCenter, f"{vmax * i / 5:.0f} ms")
        rh = h / len(self.rows)
        for i, (label, color, s) in enumerate(self.rows):
            cy = T + rh * (i + 0.5)
            bh = min(22.0, rh * 0.5)
            p.setPen(TEXT)
            p.drawText(QRectF(6, cy - 10, L - 12, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
            p.setPen(QPen(color, 1.5))
            p.drawLine(QPointF(X(s["min"]), cy), QPointF(X(s["max"]), cy))
            p.drawLine(QPointF(X(s["min"]), cy - bh / 3), QPointF(X(s["min"]), cy + bh / 3))
            p.drawLine(QPointF(X(s["max"]), cy - bh / 3), QPointF(X(s["max"]), cy + bh / 3))
            fill = QColor(color)
            fill.setAlpha(70)
            box = QRectF(X(s["p10"]), cy - bh / 2, max(2.0, X(s["p90"]) - X(s["p10"])), bh)
            p.fillRect(box, fill)
            p.drawRect(box)
            p.setPen(QPen(TEXT, 2.5))
            p.drawLine(QPointF(X(s["median"]), cy - bh / 2), QPointF(X(s["median"]), cy + bh / 2))
            p.setPen(MUTED)
            p.drawText(QRectF(X(s["max"]) + 6, cy - 10, 120, 20), Qt.AlignmentFlag.AlignVCenter,
                       f"med {s['median']:.0f}")


class MetricCard(QFrame):
    def __init__(self, title: str, unit: str = "", color: QColor = TEXT, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.t = QLabel(title.upper())
        self.t.setObjectName("cardTitle")
        self.v = QLabel("—")
        self.v.setObjectName("cardValue")
        self.v.setStyleSheet(f"color: {color.name()};")
        self.s = QLabel(unit)
        self.s.setObjectName("cardSub")
        self.unit = unit
        for wdg in (self.t, self.v, self.s):
            lay.addWidget(wdg)

    def set(self, value: str, sub: str | None = None, color: QColor | None = None):
        self.v.setText(value)
        self.s.setText(sub if sub is not None else self.unit)
        if color is not None:
            self.v.setStyleSheet(f"color: {color.name()};")


class LayerChart(QWidget):
    """Inside-out layer comparison: log-scale throughput and latency bars per layer.

    rows: list of dicts with name, throughput_mbps, latency_ms, method."""

    COLORS = [QColor("#a3e635"), QColor("#fbbf24"), DL]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.setMinimumHeight(250)

    def set_rows(self, rows):
        self.rows = rows
        self.update()

    @staticmethod
    def _log_frac(v, lo, hi):
        if not v or v <= 0:
            return 0.0
        return max(0.0, min(1.0, (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), PANEL)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(TEXT)
        p.drawText(QRectF(10, 4, self.width() - 20, 20), Qt.AlignmentFlag.AlignLeft,
                   "Performance by layer: the slowest layer caps everything above it (log scales)")
        f.setBold(False)
        p.setFont(f)
        if not self.rows:
            p.setPen(MUTED)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Run a test to compare PC -> home network -> internet")
            return
        L, T = 230, 56
        gap = 60
        colw = (self.width() - L - 20 - gap) / 2
        scales = [("Throughput (higher is better)", 1, 100_000,
                   ["1M", "10M", "100M", "1G", "10G", "100G"], "throughput_mbps"),
                  ("Latency (lower is better)", 0.01, 1000,
                   ["10µs", "100µs", "1ms", "10ms", "100ms", "1s"], "latency_ms")]
        rh = (self.height() - T - 34) / len(self.rows)
        for ci, (title, lo, hi, ticks, key) in enumerate(scales):
            x0 = L + ci * (colw + gap)
            p.setPen(MUTED)
            p.drawText(QRectF(x0, T - 26, colw, 18), Qt.AlignmentFlag.AlignLeft, title)
            for i, t in enumerate(ticks):
                x = x0 + colw * i / (len(ticks) - 1)
                p.setPen(QPen(GRID, 1))
                p.drawLine(QPointF(x, T), QPointF(x, T + rh * len(self.rows)))
                p.setPen(MUTED)
                p.drawText(QRectF(x - 30, T + rh * len(self.rows) + 4, 60, 16), Qt.AlignmentFlag.AlignCenter, t)
            for ri, row in enumerate(self.rows):
                v = row.get(key)
                cy = T + rh * (ri + 0.5)
                bh = min(26.0, rh * 0.42)
                frac = self._log_frac(v, lo, hi)
                color = self.COLORS[ri % len(self.COLORS)]
                p.fillRect(QRectF(x0, cy - bh / 2, max(2.0, colw * frac), bh), color)
                if v is not None:
                    if key == "throughput_mbps":
                        label = (f"{v / 1000:.1f} Gbps" if v >= 1000 else
                                 f"{v:.0f} Mbps" if v >= 10 else f"{v:.1f} Mbps")
                    else:
                        label = f"{v * 1000:.0f} µs" if v < 1 else f"{v:.1f} ms"
                    p.setPen(TEXT)
                    tx = x0 + colw * frac + 6
                    if frac > 0.78:  # draw inside the bar when it would overflow
                        tx = x0 + colw * frac - 84
                        p.setPen(QColor("#0b0e13"))
                    p.drawText(QRectF(tx, cy - 10, 84, 20), Qt.AlignmentFlag.AlignVCenter, label)
        fb = QFont(self.font())
        fb.setBold(True)
        for ri, row in enumerate(self.rows):
            cy = T + rh * (ri + 0.5)
            p.fillRect(QRectF(10, cy - 7, 6, 14), self.COLORS[ri % len(self.COLORS)])
            p.setFont(fb)
            p.setPen(TEXT)
            p.drawText(QRectF(22, cy - 20, L - 26, 20), Qt.AlignmentFlag.AlignVCenter, row["name"])
            p.setFont(f)
            p.setPen(MUTED)
            p.drawText(QRectF(22, cy, L - 26, 18), Qt.AlignmentFlag.AlignVCenter, row.get("method", ""))
