"""Custom painted widgets: speed gauge, line chart, latency box plot, metric card, responsive card grid."""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QGridLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget

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
        self.value = 0.0      # value currently drawn (eases toward target)
        self.target = 0.0
        self.unit = "Mbps"
        self.caption = "Ready"
        self.color = DL
        self.setMinimumSize(170, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._anim = QTimer(self)
        self._anim.setInterval(16)  # ~60 fps while the needle is moving
        self._anim.timeout.connect(self._step)

    def set_value(self, v: float, caption: str | None = None, color: QColor | None = None, unit: str | None = None):
        if unit is not None and unit != self.unit:
            self.value = 0.0  # different scale: restart the needle from zero
        self.target = max(0.0, v)
        if caption is not None:
            self.caption = caption
        if color is not None:
            self.color = color
        if unit is not None:
            self.unit = unit
        if not self._anim.isActive():
            self._anim.start()
        self.update()

    def _step(self):
        diff = self.target - self.value
        if abs(diff) < max(0.05, abs(self.target) * 0.002):
            self.value = self.target
            self._anim.stop()
        else:
            self.value += diff * 0.2
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
        # vertical extent: arc top (r + pen/2) down to the caption bottom (0.94 r) -> ~2.0 r in total
        r = max(10.0, min(self.width() * 0.44, (self.height() - 8) / 2.02))
        top = (self.height() - r * 2.02) / 2
        c = QPointF(self.width() / 2, top + r * 1.06)
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
        f.setPointSizeF(max(6.5, r * 0.065))
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
        f.setPointSizeF(max(12.0, r * 0.24))
        f.setBold(True)
        p.setFont(f)
        p.setPen(TEXT)
        txt = f"{self.value:.1f}" if self.value < 100 else f"{self.value:.0f}"
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.15, 2 * r, r * 0.35), Qt.AlignmentFlag.AlignCenter, txt)
        f.setPointSizeF(max(7.5, r * 0.08))
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
        self.setMinimumHeight(130)

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
        L, R, T, B = 52, 14, 34, 26
        w, h = self.width() - L - R, self.height() - T - B
        p.setPen(TEXT)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(10, 4, self.width() - 20, 20), Qt.AlignmentFlag.AlignLeft, self.title)
        title_w = p.fontMetrics().horizontalAdvance(self.title)
        f.setBold(False)
        p.setFont(f)
        if self.y_label:  # unit next to the title, so it never collides with the axis labels
            p.setPen(MUTED)
            p.drawText(QRectF(10 + title_w + 8, 4, 120, 20), Qt.AlignmentFlag.AlignLeft, f"({self.y_label})")
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

        n = max(1, min(4, int(h // 26)))  # fewer gridlines on short charts so labels don't overlap
        for i in range(n + 1):
            y = T + h * i / n
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(L, y), QPointF(L + w, y))
            p.setPen(MUTED)
            v = ymax * (n - i) / n
            p.drawText(QRectF(0, y - 8, L - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{v:.0f}" if v >= 10 else f"{v:.1f}")
        p.setPen(MUTED)
        for i in range(6):
            x = xmin + (xmax - xmin) * i / 5
            px = L + w * i / 5
            label = self.x_ticks[min(len(self.x_ticks) - 1, round(x))] if self.x_ticks else f"{x:.0f}{self.x_label}"
            p.drawText(QRectF(px - 45, T + h + 4, 90, 16), Qt.AlignmentFlag.AlignCenter, label)

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
        self.setMinimumHeight(150)

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


class ElidedLabel(QLabel):
    """Single-line label that shortens with "…" instead of forcing its parent wider; full text in the tooltip."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str):  # noqa: N802 (Qt naming)
        self._full = text
        self.setToolTip(text)
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        w = max(10, self.contentsRect().width())
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, w))


class MetricCard(QFrame):
    def __init__(self, title: str, unit: str = "", color: QColor = TEXT, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(0)
        self.t = ElidedLabel(title.upper())
        self.t.setObjectName("cardTitle")
        self.v = ElidedLabel("—")
        self.v.setObjectName("cardValue")
        self._color = color.name()
        self.v.setStyleSheet(f"color: {self._color};")
        self.s = ElidedLabel(unit)
        self.s.setObjectName("cardSub")
        self.unit = unit
        for wdg in (self.t, self.v, self.s):
            lay.addWidget(wdg)

    def set(self, value: str, sub: str | None = None, color: QColor | None = None):
        sub = sub if sub is not None else self.unit
        if value != self.v.text():   # skip no-op updates: each one costs a relayout
            self.v.setText(value)
        if sub != self.s.text():
            self.s.setText(sub)
        if color is not None and color.name() != self._color:
            self._color = color.name()
            self.v.setStyleSheet(f"color: {self._color};")


class CardGrid(QWidget):
    """Lays cards out in as many columns as fit (2..max_cols) and reflows when resized."""

    def __init__(self, cards, min_card_width: int = 160, max_cols: int = 5, parent=None):
        super().__init__(parent)
        self.cards = list(cards)
        self.min_card_width = min_card_width
        self.max_cols = max_cols
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.cols = 0
        self._reflow(2)

    def _reflow(self, cols: int):
        if cols == self.cols:
            return
        self.cols = cols
        for c in self.cards:
            self.grid.removeWidget(c)
        for col in range(self.max_cols):
            self.grid.setColumnStretch(col, 1 if col < cols else 0)
        for i, c in enumerate(self.cards):
            self.grid.addWidget(c, i // cols, i % cols)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        sp = self.grid.spacing()
        cols = max(2, min(self.max_cols, (self.width() + sp) // (self.min_card_width + sp)))
        if cols != self.cols:
            QTimer.singleShot(0, lambda: self._reflow(cols))  # not from inside the resize itself


class SmoothProgress(QProgressBar):
    """Progress bar (0..1000) that glides to new values instead of jumping."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 1000)
        self._target = 0
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._step)

    def set_target(self, v: float):
        self._target = max(0, min(1000, int(v)))
        if self._target < self.value():
            self._anim.stop()
            self.setValue(self._target)  # resets jump straight back
        elif not self._anim.isActive():
            self._anim.start()

    def _step(self):
        diff = self._target - self.value()
        if diff <= 2:
            self.setValue(self._target)
            self._anim.stop()
        else:
            self.setValue(self.value() + max(1, int(diff * 0.15)))


class LayerChart(QWidget):
    """Inside-out layer comparison: log-scale throughput and latency bars per layer.

    rows: list of dicts with name, throughput_mbps, latency_ms, method."""

    COLORS = [QColor("#a3e635"), QColor("#fbbf24"), DL]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.setMinimumHeight(200)

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


def paint_logo(p: QPainter, size: float) -> None:
    """NetQuality mark: a dark rounded tile with a speedometer arc (cyan → violet, download → upload)
    and a needle near the top of the scale. Drawn in code so it scales cleanly from 16 px to 256 px."""
    from PyQt6.QtGui import QConicalGradient, QLinearGradient

    s = float(size)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QLinearGradient(0, 0, 0, s)
    bg.setColorAt(0, QColor("#232a38"))
    bg.setColorAt(1, QColor("#10131a"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)

    c = QPointF(s / 2, s * 0.56)
    r = s * 0.31
    width = max(2.0, s * 0.11)
    rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
    start, span = 225.0, -270.0
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#2f3747"), width, cap=Qt.PenCapStyle.RoundCap))
    p.drawArc(rect, int(start * 16), int(span * 16))
    frac = 0.78
    # conical gradients sweep counter-clockwise; start at the needle end so the colour runs violet → cyan back
    # to the arc start, i.e. cyan at low speed and violet near the top of the scale
    grad2 = QConicalGradient(c, start + span * frac)
    grad2.setColorAt(0.0, UL)
    grad2.setColorAt(frac * 270 / 360, DL)
    grad2.setColorAt(1.0, UL)
    p.setPen(QPen(grad2, width, cap=Qt.PenCapStyle.RoundCap))
    p.drawArc(rect, int(start * 16), int(span * frac * 16))

    a = math.radians(start + span * frac)
    tip = QPointF(c.x() + math.cos(a) * r * 0.72, c.y() - math.sin(a) * r * 0.72)
    p.setPen(QPen(TEXT, max(1.5, s * 0.06), cap=Qt.PenCapStyle.RoundCap))
    p.drawLine(c, tip)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(TEXT)
    p.drawEllipse(c, s * 0.065, s * 0.065)


def logo_image(size: int):
    from PyQt6.QtGui import QImage
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    paint_logo(p, size)
    p.end()
    return img


def app_icon():
    from PyQt6.QtGui import QIcon, QPixmap
    icon = QIcon()
    for sz in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(logo_image(sz)))
    return icon
