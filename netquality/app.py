"""PyQt6 GUI."""
from __future__ import annotations

import html
import json
import sys
import traceback

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                             QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                             QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import diagnosis
from .engine import Cancelled, SpeedTest, TestConfig
from .history import History
from .widgets import (BAD, DL, IDLE, MUTED, TEXT, UL, WARN, BoxPlot, Gauge, LayerChart, LineChart,
                      MetricCard)

STYLE = """
QWidget { background: #12151c; color: #e6e9ef; font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 10pt; }
QPushButton { background: #22d3ee; color: #0b0e13; border: none; border-radius: 6px; padding: 8px 22px; font-weight: 600; }
QPushButton:hover { background: #67e8f9; }
QPushButton:disabled { background: #2a303c; color: #8a93a6; }
QPushButton#secondary { background: #2a303c; color: #e6e9ef; font-weight: 500; padding: 6px 14px; }
QPushButton#secondary:hover { background: #363d4c; }
QPushButton#stop { background: #f87171; }
QFrame#card { background: #1a1e27; border-radius: 8px; }
QLabel#cardTitle { color: #8a93a6; font-size: 8pt; letter-spacing: 1px; background: transparent; }
QLabel#cardValue { font-size: 18pt; font-weight: 700; background: transparent; }
QLabel#cardSub { color: #8a93a6; font-size: 8pt; background: transparent; }
QLabel#verdict { background: #1a1e27; border-radius: 8px; padding: 10px 14px; font-size: 10.5pt; }
QLabel#muted { color: #8a93a6; }
QTabWidget::pane { border: 1px solid #2a303c; border-radius: 6px; top: -1px; }
QTabBar::tab { background: #1a1e27; color: #8a93a6; padding: 7px 16px; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
QTabBar::tab:selected { background: #2a303c; color: #e6e9ef; }
QProgressBar { background: #1a1e27; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #22d3ee; border-radius: 3px; }
QSpinBox { background: #1a1e27; border: 1px solid #2a303c; border-radius: 4px; padding: 3px 6px; }
QTreeWidget, QTableWidget, QTextBrowser, QPlainTextEdit { background: #1a1e27; border: none; alternate-background-color: #1e232d; }
QHeaderView::section { background: #232835; color: #8a93a6; border: none; padding: 5px; }
QTableWidget { gridline-color: #2a303c; }
QToolTip { background: #232835; color: #e6e9ef; border: 1px solid #363d4c; }
"""

SEV_COLOR = {"critical": "#f87171", "warning": "#fbbf24", "info": "#60a5fa", "good": "#4ade80"}
SEV_ICON = {"critical": "✖", "warning": "⚠", "info": "ℹ", "good": "✔"}
STATUS_COLOR = {"good": "#4ade80", "fair": "#fbbf24", "poor": "#f87171"}
AREA_LABEL = {"pc": "This PC", "local": "Home network", "router": "Router / modem", "isp": "ISP / internet", "general": "General"}


def fmt(v, digits=1, unit=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        s = f"{v:.{digits}f}"
    else:
        s = str(v)
    return f"{s} {unit}".strip()


def grade_color(g: str) -> QColor:
    return {"A+": IDLE, "A": IDLE, "B": WARN, "C": WARN}.get(g, BAD if g in ("D", "F") else MUTED)


class Worker(QThread):
    event = pyqtSignal(str, dict)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, cfg: TestConfig):
        super().__init__()
        self.test = SpeedTest(cfg, lambda e, d: self.event.emit(e, d))

    def run(self):
        try:
            self.done.emit(self.test.run())
        except Cancelled:
            self.failed.emit("Test cancelled.")
        except Exception as e:  # surface anything unexpected in the UI
            self.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

    def cancel(self):
        self.test.cancel()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NetQuality: local internet quality test")
        self.resize(1180, 860)
        self.history = History()
        self.worker: Worker | None = None
        self.result: dict | None = None

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)

        # ---------------- header
        hdr = QHBoxLayout()
        title = QLabel("NetQuality")
        title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        hdr.addWidget(title)
        self.server_lbl = QLabel("Server: speed.cloudflare.com (nearest PoP)")
        self.server_lbl.setObjectName("muted")
        hdr.addWidget(self.server_lbl, 1)
        hdr.addWidget(QLabel("Duration/dir (s)"))
        self.dur = QSpinBox()
        self.dur.setRange(5, 60)
        self.dur.setValue(10)
        hdr.addWidget(self.dur)
        hdr.addWidget(QLabel("Streams"))
        self.streams = QSpinBox()
        self.streams.setRange(1, 16)
        self.streams.setValue(6)
        self.streams.setToolTip("Parallel TCP connections. More streams saturate fast links better;\n"
                                "1 stream shows single-connection performance (like one browser download).")
        hdr.addWidget(self.streams)
        self.export_btn = QPushButton("Export JSON")
        self.export_btn.setObjectName("secondary")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_json)
        hdr.addWidget(self.export_btn)
        self.start_btn = QPushButton("START")
        self.start_btn.clicked.connect(self.toggle)
        hdr.addWidget(self.start_btn)
        outer.addLayout(hdr)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(6)
        outer.addWidget(self.progress)

        # ---------------- top: gauge + cards
        top = QHBoxLayout()
        self.gauge = Gauge()
        top.addWidget(self.gauge, 4)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.cards = {
            "down": MetricCard("Download", "Mbps sustained", DL),
            "up": MetricCard("Upload", "Mbps sustained", UL),
            "ping": MetricCard("Latency (idle)", "ms median", TEXT),
            "jitter": MetricCard("Jitter", "ms", TEXT),
            "loss": MetricCard("Packet loss", "% ICMP", TEXT),
            "bloat": MetricCard("Bufferbloat", "grade", TEXT),
            "loaded": MetricCard("Latency under load", "ms (down / up)", TEXT),
            "router": MetricCard("Router hop", "ms / loss", TEXT),
            "localhost": MetricCard("This PC (localhost)", "TCP loopback", QColor("#a3e635")),
            "lan": MetricCard("Home network capacity", "estimated, to router", WARN),
        }
        for i, c in enumerate(self.cards.values()):
            grid.addWidget(c, i // 2, i % 2)
        gw = QWidget()
        gw.setLayout(grid)
        top.addWidget(gw, 5)
        outer.addLayout(top, 4)

        self.verdict = QLabel("Press START to run a full quality test (≈ 45 s, uses ~ 100–1000 MB depending on speed).")
        self.verdict.setObjectName("verdict")
        self.verdict.setWordWrap(True)
        outer.addWidget(self.verdict)

        # ---------------- tabs
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 6)

        self.diag_view = QTextBrowser()
        self.diag_view.setOpenExternalLinks(True)
        self.tabs.addTab(self.diag_view, "Diagnosis && fixes")

        suit = QWidget()
        sl = QHBoxLayout(suit)
        self.act_table = self._table(["Activity", "Rating", "Why"])
        self.time_table = self._table(["Task", "Estimated time"])
        sl.addWidget(self.act_table, 3)
        sl.addWidget(self.time_table, 2)
        self.tabs.addTab(suit, "What it's good for")

        layers = QWidget()
        ll = QVBoxLayout(layers)
        lsplit = QSplitter(Qt.Orientation.Vertical)
        self.layer_chart = LayerChart()
        lsplit.addWidget(self.layer_chart)
        lower = QWidget()
        lowl = QHBoxLayout(lower)
        lowl.setContentsMargins(0, 0, 0, 0)
        self.layer_table = self._table(["Layer", "Latency", "Throughput", "Details"])
        self.sweep_table = self._table(["Packet size", "Replies", "Min RTT", "Median RTT", "Round trip"])
        self.sweep_table.setToolTip("ICMP echoes of increasing size to the router. Min RTT grows with size;\n"
                                    "the slope of that line is the link's serialization time, i.e. its capacity.")
        lowl.addWidget(self.layer_table, 3)
        lowl.addWidget(self.sweep_table, 2)
        lsplit.addWidget(lower)
        ll.addWidget(lsplit)
        self.tabs.addTab(layers, "Layers")

        charts = QWidget()
        cl = QVBoxLayout(charts)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tp_chart = LineChart("Throughput over time", "s", "Mbps")
        self.lat_chart = LineChart("Latency over time: idle vs. under load", "s", "ms")
        self.box = BoxPlot("Latency distribution (whiskers min–max, box p10–p90, line median)")
        for wdg in (self.tp_chart, self.lat_chart, self.box):
            split.addWidget(wdg)
        cl.addWidget(split)
        self.tabs.addTab(charts, "Charts")

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Metric", "Value", "What it means"])
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tabs.addTab(self.tree, "All metrics")

        hist = QWidget()
        hl = QVBoxLayout(hist)
        hb = QHBoxLayout()
        for label, fn in (("Open selected", self.open_history), ("Delete selected", self.delete_history),
                          ("Export CSV", self.export_csv)):
            b = QPushButton(label)
            b.setObjectName("secondary")
            b.clicked.connect(fn)
            hb.addWidget(b)
        hb.addStretch(1)
        hl.addLayout(hb)
        hsplit = QSplitter(Qt.Orientation.Vertical)
        self.hist_table = self._table(["#", "When", "Down", "Up", "Ping", "Jitter", "Loss %", "Bloat",
                                       "Link", "Server"])
        self.hist_table.doubleClicked.connect(self.open_history)
        self.hist_chart = LineChart("History trend", "", "Mbps")
        hsplit.addWidget(self.hist_table)
        hsplit.addWidget(self.hist_chart)
        hl.addWidget(hsplit)
        self.tabs.addTab(hist, "History")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.log, "Log")

        self.refresh_history()
        self.diag_view.setHtml(self._intro_html())

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.setWordWrap(True)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    @staticmethod
    def _intro_html():
        return ("<h3>What this test measures</h3><ul>"
                "<li><b>Your local link</b>: Wi-Fi signal, band, negotiated rate, and latency/loss to your router.</li>"
                "<li><b>Idle latency</b> via HTTP, ICMP ping and the server-side TCP RTT, plus jitter and packet loss.</li>"
                "<li><b>DNS speed</b> (cached and uncached) and connect times to major networks.</li>"
                "<li><b>Download/upload</b> with parallel streams: sustained, peak, percentiles, stability, ramp-up "
                "and server-reported TCP retransmissions.</li>"
                "<li><b>Latency under load (bufferbloat)</b>, checked both to the internet and to your router, "
                "so it can tell whether the queue is in your Wi-Fi or at the modem/ISP.</li></ul>"
                "<p>Afterwards this tab lists detected problems, where they come from, and how to fix them.</p>"
                "<p style='color:#8a93a6'>Test server: Cloudflare's public speed endpoints (anycast, nearest PoP). "
                "No ads, no tracking, results stay on this PC (~/.netquality).</p>")

    def _log(self, msg):
        self.log.appendPlainText(msg)

    # ------------------------------------------------------------------ run control
    def toggle(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.start_btn.setEnabled(False)
            self.start_btn.setText("Stopping…")
            return
        self.reset_ui()
        cfg = TestConfig(download_seconds=self.dur.value(), upload_seconds=self.dur.value(),
                         streams=self.streams.value())
        self.worker = Worker(cfg)
        self.worker.event.connect(self.on_event)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_finished)
        self.start_btn.setText("STOP")
        self.start_btn.setObjectName("stop")
        self.start_btn.setStyle(self.start_btn.style())
        self.dur.setEnabled(False)
        self.streams.setEnabled(False)
        self.worker.start()

    def reset_ui(self):
        for c in self.cards.values():
            c.set("—")
        self.tp_chart.clear()
        self.lat_chart.clear()
        self.box.set_rows([])
        self.layer_chart.set_rows([])
        self.progress.setValue(0)
        self.gauge.set_value(0, "Starting…", DL, "Mbps")
        self.verdict.setText("Testing…")
        self.log.clear()
        self.idle_ms = None

    def on_finished(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("START")
        self.start_btn.setObjectName("")
        self.start_btn.setStyle(self.start_btn.style())
        self.dur.setEnabled(True)
        self.streams.setEnabled(True)

    def on_failed(self, msg):
        self._log(msg)
        self.gauge.set_value(0, "Stopped", MUTED)
        self.verdict.setText(html.escape(msg.splitlines()[0]))

    # ------------------------------------------------------------------ live events
    def on_event(self, ev: str, d: dict):
        if ev == "log":
            self._log(d["msg"])
        elif ev == "progress":
            self.progress.setValue(int(d["value"] * 1000))
        elif ev == "phase":
            self.verdict.setText(d["label"] + "…")
            color = UL if d["name"] == "upload" else DL
            unit = "Mbps" if d["name"] in ("download", "upload") else "ms"
            self.gauge.set_value(0, d["label"].replace("Testing ", "").capitalize(), color, unit)
        elif ev == "meta":
            m = d
            loc = ", ".join(x for x in (m.get("city"), m.get("country") or m.get("loc")) if x)
            self.server_lbl.setText(f"Server: Cloudflare {m.get('colo', '?')}  ·  Your IP {m.get('ip', '?')}"
                                    + (f"  ·  AS{m['asn']}" if m.get("asn") else "") + (f"  ·  {loc}" if loc else ""))
        elif ev == "local":
            self._show_local(d.get("localhost") or {}, d.get("router") or {})
        elif ev == "latency_sample":
            if d["kind"] == "idle":
                self.gauge.set_value(d["ms"], "Latency", IDLE, "ms")
                self.lat_chart.append("idle", IDLE, d["i"] * 0.1, d["ms"])
            else:
                color = DL if d["kind"] == "download" else UL
                self.lat_chart.append(f"loaded ({d['kind']})", color, d["t"], d["ms"])
        elif ev == "idle_latency":
            http_ = d.get("http") or {}
            self.idle_ms = http_.get("median")
            self.cards["ping"].set(fmt(self.idle_ms), f"ms median · min {fmt(http_.get('min'))}")
            icmp = d.get("icmp") or {}
            jit = icmp.get("jitter_ms", http_.get("jitter_ms"))
            self.cards["jitter"].set(fmt(jit), "ms (ICMP)" if icmp.get("jitter_ms") is not None else "ms (HTTP)")
            loss = icmp.get("loss_pct")
            self.cards["loss"].set(fmt(loss), f"% of {icmp.get('sent', 0)} pings to {icmp.get('target', '?')}",
                                   IDLE if loss == 0 else WARN if (loss or 0) < 2 else BAD)
            gw = d.get("gateway") or {}
            if gw.get("summary"):
                self.cards["router"].set(fmt(gw["summary"]["median"]), f"ms · {fmt(gw.get('loss_pct'))}% loss")
            # idle latency series uses probe index; rescale onto 0..2 s so it is visible beside loaded series
            self.lat_chart.set_series("idle", IDLE, [(i * 0.1, v) for i, v in enumerate(d.get("http_samples") or [])])
        elif ev == "throughput":
            direction = d["direction"]
            color = DL if direction == "download" else UL
            self.gauge.set_value(d["mbps"], direction.capitalize(), color, "Mbps")
            self.tp_chart.append(direction, color, d["t"], d["mbps"])
            self.cards["down" if direction == "download" else "up"].set(fmt(d["mbps"]), "Mbps (live)")

    def _show_local(self, lh: dict, rt: dict):
        if lh.get("tcp_multi_stream_mbps"):
            self.cards["localhost"].set(diagnosis.fmt_rate(lh["tcp_multi_stream_mbps"]),
                                        f"TCP loopback · {(lh.get('tcp_rtt_us') or {}).get('median', 0):.0f} µs RTT")
        cap = rt.get("capacity_mbps")
        if cap:
            ok = rt.get("capacity_reliable")
            self.cards["lan"].set(("≈ " if ok else "~ ") + diagnosis.fmt_rate(cap),
                                  f"to router · {rt.get('base_rtt_ms') or 0:.1f} ms base RTT"
                                  + ("" if ok else " · low confidence"))
        elif rt.get("error"):
            self.cards["lan"].set("—", rt["error"])

    # ------------------------------------------------------------------ results
    def on_done(self, r: dict):
        self.result = r
        try:
            self.history.add(r)
        except Exception as e:
            self._log(f"Could not save history: {e}")
        self.show_result(r)
        self.refresh_history()
        self.export_btn.setEnabled(True)
        self._log(f"Done in {r.get('duration_s', 0):.0f} s, {r.get('data_used_mb', 0):.0f} MB transferred.")

    def show_result(self, r: dict):
        dl, ul = r.get("download") or {}, r.get("upload") or {}
        lat = r.get("latency") or {}
        self.cards["down"].set(fmt(dl.get("mbps")), f"Mbps sustained · peak {fmt(dl.get('peak_mbps'), 0)}")
        self.cards["up"].set(fmt(ul.get("mbps")), f"Mbps sustained · peak {fmt(ul.get('peak_mbps'), 0)}")
        self.on_event("idle_latency", lat)
        grades = [g for g in (dl.get("bufferbloat_grade"), ul.get("bufferbloat_grade")) if g and g != "?"]
        worst = max(grades, key=["A+", "A", "B", "C", "D", "F"].index) if grades else "?"
        self.cards["bloat"].set(worst, f"+{fmt(dl.get('bufferbloat_ms'), 0)} / +{fmt(ul.get('bufferbloat_ms'), 0)} ms",
                                grade_color(worst))
        self.cards["loaded"].set(f"{fmt((dl.get('loaded_latency') or {}).get('median'), 0)} / "
                                 f"{fmt((ul.get('loaded_latency') or {}).get('median'), 0)}", "ms median (down / up)")
        self.gauge.set_value(dl.get("mbps") or 0, "Download (final)", DL, "Mbps")

        self.tp_chart.clear()
        self.tp_chart.set_series("download", DL, dl.get("series") or [])
        self.tp_chart.set_series("upload", UL, ul.get("series") or [])
        self.lat_chart.clear()
        self.lat_chart.set_series("idle", IDLE, [(i * 0.1, v) for i, v in enumerate(lat.get("http_samples") or [])])
        self.lat_chart.set_series("loaded (download)", DL, dl.get("loaded_samples") or [])
        self.lat_chart.set_series("loaded (upload)", UL, ul.get("loaded_samples") or [])
        gw_idle = (lat.get("gateway") or {}).get("summary")
        self.box.set_rows([
            ("Idle (HTTP)", IDLE, lat.get("http")),
            ("Idle (ICMP)", IDLE, (lat.get("icmp") or {}).get("summary")),
            ("Router idle", QColor("#a3e635"), gw_idle),
            ("Router during DL", QColor("#0891b2"), ((dl.get("gateway_loaded") or {}).get("summary"))),
            ("Under download", DL, dl.get("loaded_latency")),
            ("Under upload", UL, ul.get("loaded_latency")),
        ])

        self._show_local(r.get("localhost") or {}, r.get("router") or {})
        self._fill_layers(r)
        self.verdict.setText("<b>Verdict:</b> " + html.escape(diagnosis.bottleneck_summary(r)))
        self.diag_view.setHtml(self._diagnosis_html(r))
        self._fill_activities(r)
        self._fill_tree(r)
        self.progress.setValue(1000)

    def _diagnosis_html(self, r):
        issues = diagnosis.diagnose(r)
        counts = {s: sum(1 for i in issues if i["severity"] == s) for s in SEV_COLOR}
        parts = [f"<p style='color:#8a93a6'>{counts['critical']} critical · {counts['warning']} warnings · "
                 f"{counts['info']} notes</p>"]
        for i in issues:
            col = SEV_COLOR[i["severity"]]
            parts.append(
                f"<table width='100%' cellpadding='8' style='margin-bottom:6px;background:#1e232d'>"
                f"<tr><td width='4' style='background:{col}'></td><td>"
                f"<span style='color:{col};font-size:11pt'><b>{SEV_ICON[i['severity']]} {html.escape(i['title'])}</b></span>"
                f"&nbsp;&nbsp;<span style='color:#8a93a6'>[{AREA_LABEL.get(i['area'], i['area'])}]</span><br>"
                f"<span>{html.escape(i['detail'])}</span>")
            if i["fixes"]:
                parts.append("<br><b style='color:#e6e9ef'>How to improve:</b><ul style='margin-top:2px'>" +
                             "".join(f"<li>{html.escape(f)}</li>" for f in i["fixes"]) + "</ul>")
            parts.append("</td></tr></table>")
        parts.append("<p style='color:#8a93a6'>Tip: run once on Wi-Fi and once on Ethernet next to the router. "
                     "If the wired result is much better, the problem is in your Wi-Fi, not your ISP. "
                     "Testing at several times of day shows ISP congestion during peak hours.</p>")
        return "".join(parts)

    def _fill_layers(self, r):
        rows = diagnosis.layers(r)
        self.layer_chart.set_rows(rows)
        t = self.layer_table
        t.setRowCount(len(rows))
        for i, row in enumerate(rows):
            lat = row.get("latency_ms")
            vals = [row["name"],
                    "—" if lat is None else (f"{lat * 1000:.0f} µs" if lat < 1 else f"{lat:.1f} ms"),
                    diagnosis.fmt_rate(row.get("throughput_mbps")), row["detail"]]
            for j, v in enumerate(vals):
                t.setItem(i, j, QTableWidgetItem(v))
        hh = t.horizontalHeader()
        for c in range(3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        t.resizeRowsToContents()
        sizes = (r.get("router") or {}).get("sizes") or []
        st = self.sweep_table
        st.setRowCount(len(sizes))
        for i, sz in enumerate(sizes):
            rate = (sz["size"] * 16 / sz["median_ms"] / 1000) if sz.get("median_ms") else None
            vals = [f"{sz['size']:,} B", f"{sz['received']}/{sz['sent']}", fmt(sz.get("min_ms"), 2, "ms"),
                    fmt(sz.get("median_ms"), 2, "ms"), diagnosis.fmt_rate(rate)]
            for j, v in enumerate(vals):
                st.setItem(i, j, QTableWidgetItem(v))
        st.resizeColumnsToContents()

    def _fill_activities(self, r):
        acts = diagnosis.activities(r)
        self.act_table.setRowCount(len(acts))
        for row, a in enumerate(acts):
            self.act_table.setItem(row, 0, QTableWidgetItem(a["name"]))
            it = QTableWidgetItem({"good": "● Good", "fair": "● Fair", "poor": "● Poor"}[a["status"]])
            it.setForeground(QColor(STATUS_COLOR[a["status"]]))
            self.act_table.setItem(row, 1, it)
            self.act_table.setItem(row, 2, QTableWidgetItem(a["detail"]))
        hh = self.act_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.act_table.resizeRowsToContents()
        times = diagnosis.transfer_times(r)
        self.time_table.setRowCount(len(times))
        for row, (task, t) in enumerate(times):
            self.time_table.setItem(row, 0, QTableWidgetItem(task))
            self.time_table.setItem(row, 1, QTableWidgetItem(t))
        self.time_table.resizeColumnsToContents()

    def _fill_tree(self, r):
        self.tree.clear()

        def section(name):
            it = QTreeWidgetItem([name])
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            self.tree.addTopLevelItem(it)
            return it

        def row(parent, label, value, expl=""):
            QTreeWidgetItem(parent, [label, value, expl])

        link = r.get("link") or {}
        wifi = link.get("wifi") or {}
        s = section("Local network")
        row(s, "Connection type", str(link.get("medium")), "Wi-Fi is the most common source of latency spikes and loss")
        row(s, "Adapter", str(link.get("adapter")))
        row(s, "Link rate", fmt(link.get("link_mbps"), 0, "Mbps"), "Negotiated PHY rate; real throughput ≈ 50–70% on Wi-Fi")
        if wifi:
            row(s, "Wi-Fi SSID / band / channel", f"{wifi.get('ssid', '?')} / {wifi.get('band', '?')} / {wifi.get('channel', '?')}")
            row(s, "Wi-Fi standard", str(wifi.get("radio", "?")))
            row(s, "Signal (RSSI)", f"{fmt(wifi.get('rssi_dbm'), 0, 'dBm')}  ({fmt(wifi.get('signal_pct'), 0, '%')})",
                "> −60 excellent, −60…−67 good, −67…−75 fair, < −75 weak")
            row(s, "Rx / Tx rate", f"{fmt(wifi.get('rx_rate_mbps'), 0)} / {fmt(wifi.get('tx_rate_mbps'), 0)} Mbps")
        row(s, "Default gateway", str(link.get("gateway")))
        gw = (r.get("latency") or {}).get("gateway") or {}
        if gw.get("summary"):
            g = gw["summary"]
            row(s, "Router latency (median / p95 / max)", f"{g['median']:.1f} / {g['p95']:.1f} / {g['max']:.1f} ms",
                "Should be ~1–5 ms on Wi-Fi, < 1 ms wired")
            row(s, "Router jitter / loss", f"{fmt(gw.get('jitter_ms'))} ms / {fmt(gw.get('loss_pct'))}%")

        lh = r.get("localhost") or {}
        if lh:
            s = section("This PC (localhost)")
            row(s, "TCP loopback, 1 stream", diagnosis.fmt_rate(lh.get("tcp_1_stream_mbps")),
                "One connection through the OS TCP/IP stack; modern PCs: 5–30 Gbps")
            row(s, f"TCP loopback, {lh.get('streams', '?')} streams", diagnosis.fmt_rate(lh.get("tcp_multi_stream_mbps")),
                "Ceiling for all network activity on this PC")
            tr = lh.get("tcp_rtt_us") or {}
            if tr:
                row(s, "TCP round trip (median / p95)", f"{tr['median']:.0f} / {tr['p95']:.0f} µs",
                    "Stack + scheduler overhead per packet; 10–60 µs is normal")
            ic = lh.get("icmp_ms") or {}
            if ic:
                row(s, "ICMP ping 127.0.0.1 (median)", fmt(ic.get("median"), 2, "ms"))
        rt = r.get("router") or {}
        if rt.get("sizes"):
            s = section("Router hop: packet-size sweep")
            row(s, "Estimated link capacity", diagnosis.fmt_rate(rt.get("capacity_mbps")),
                "From the slope of min RTT vs packet size (average of both directions)")
            fit = rt.get("fit") or {}
            row(s, "Fit quality (R²)", fmt(fit.get("r2"), 3),
                "≥ 0.85 = reliable estimate; lower means the hop was too noisy")
            row(s, "Base RTT (32 B)", fmt(rt.get("base_rtt_ms"), 2, "ms"))
            row(s, "64 KB echo round-trip rate", diagnosis.fmt_rate(rt.get("large_packet_roundtrip_mbps")),
                "One large packet there and back: a lower bound for the link")
            row(s, "Loss: small / large packets",
                f"{fmt(rt.get('small_packet_loss_pct'))}% / {fmt(rt.get('large_packet_loss_pct'))}%",
                "Loss only on large packets suggests interference or fragmentation trouble")
            for sz in rt["sizes"]:
                row(s, f"    {sz['size']:,} B", f"min {fmt(sz.get('min_ms'), 2)} · med {fmt(sz.get('median_ms'), 2)} ms",
                    f"{sz['received']}/{sz['sent']} replies")

        c = r.get("connection") or {}
        s = section("Connection setup (to speed.cloudflare.com)")
        row(s, "DNS lookup", fmt(c.get("dns_ms"), 1, "ms"))
        row(s, "TCP connect", fmt(c.get("tcp_ms"), 1, "ms"), "≈ one network round trip")
        row(s, "TLS handshake", fmt(c.get("tls_ms"), 1, "ms"), "TLS 1.3 needs one round trip")
        row(s, "Time to first byte", fmt(c.get("ttfb_ms"), 1, "ms"), "Request → first response byte")
        row(s, "Total to first byte", fmt(c.get("total_ms"), 1, "ms"), "What every new site connection costs")
        row(s, "IP version / remote", f"IPv{c.get('ip_version', '?')} / {c.get('remote_ip', '?')}")
        row(s, "IPv6 available", str(c.get("ipv6_available")))
        row(s, "TLS / cipher", f"{c.get('tls_version')} / {c.get('cipher')}")
        meta = r.get("meta") or {}
        row(s, "Cloudflare PoP / HTTP", f"{meta.get('colo', '?')} / {meta.get('http', '?')}")

        lat = r.get("latency") or {}
        s = section("Idle latency")
        for label, summ, expl in (
                ("HTTP RTT", lat.get("http"), "Application-level request/response on a warm connection"),
                ("ICMP ping", (lat.get("icmp") or {}).get("summary"), f"to {(lat.get('icmp') or {}).get('target')}"),
                ("Server-side TCP RTT", lat.get("server_tcp_rtt_ms"), "Smoothed RTT the Cloudflare kernel observes")):
            if summ:
                row(s, label, f"min {summ['min']:.1f} · med {summ['median']:.1f} · p90 {summ['p90']:.1f} · "
                              f"max {summ['max']:.1f} ms", expl)
        icmp = lat.get("icmp") or {}
        row(s, "Jitter (ICMP / HTTP)", f"{fmt(icmp.get('jitter_ms'))} / {fmt((lat.get('http') or {}).get('jitter_ms'))} ms",
            "Mean variation between consecutive packets; < 10 ms ideal for calls/games")
        row(s, "Packet loss", f"{fmt(icmp.get('loss_pct'))}% ({icmp.get('received', 0)}/{icmp.get('sent', 0)})",
            "Anything above 0% is noticeable in calls and games")

        d = r.get("dns") or {}
        s = section("DNS & reachability")
        row(s, "DNS popular names (median)", fmt((d.get("popular_ms") or {}).get("median"), 1, "ms"), "Usually cached: should be < 20 ms")
        row(s, "DNS uncached lookup (median)", fmt((d.get("uncached_ms") or {}).get("median"), 1, "ms"), "Full recursive lookup: < 100 ms is good")
        for t in r.get("targets") or []:
            row(s, f"TCP connect: {t['name']}", fmt(t.get("median_ms"), 1, "ms"), t["host"])

        for key, title in (("download", "Download"), ("upload", "Upload")):
            x = r.get(key) or {}
            s = section(title)
            row(s, "Sustained speed", fmt(x.get("mbps"), 2, "Mbps"), "Average after the TCP warm-up period: the headline number")
            row(s, "Overall average (incl. warm-up)", fmt(x.get("mbps_overall"), 2, "Mbps"))
            row(s, "Peak (0.5 s window)", fmt(x.get("peak_mbps"), 2, "Mbps"))
            rates = x.get("rates") or {}
            if rates:
                row(s, "p10 / median / p90", f"{rates['p10']:.1f} / {rates['median']:.1f} / {rates['p90']:.1f} Mbps",
                    "p10 is the 'bad moment' speed you can count on")
            row(s, "Stability", fmt(x.get("stability"), 0, "/ 100"), "100 = perfectly flat; < 65 means large swings")
            row(s, "Ramp-up to 90%", fmt(x.get("ramp_up_s"), 1, "s"), "How quickly the connection reaches full speed")
            ll = x.get("loaded_latency") or {}
            if ll:
                row(s, "Latency under load", f"med {ll['median']:.0f} · p90 {ll['p90']:.0f} · max {ll['max']:.0f} ms")
            row(s, "Bufferbloat", f"+{fmt(x.get('bufferbloat_ms'), 0)} ms  (grade {x.get('bufferbloat_grade')})",
                "Latency increase while saturated. A+ < 5, A < 30, B < 60, C < 200, D < 400 ms")
            gl = (x.get("gateway_loaded") or {}).get("summary")
            if gl:
                row(s, "Router latency under load", f"med {gl['median']:.1f} · p95 {gl['p95']:.1f} ms",
                    "If this rises too, the queue is in the Wi-Fi/router")
            st = x.get("server_tcp")
            if st:
                row(s, "Server TCP retransmissions", f"{fmt(st.get('retrans_pct'), 2, '%')} ({st.get('retrans')}/{st.get('sent')} pkts)",
                    "Reported by Cloudflare's server; > 2% suggests loss/congestion")
            row(s, "Data transferred", fmt((x.get("bytes") or 0) / 1e6, 0, "MB"),
                f"{x.get('streams')} parallel streams, {x.get('errors', 0)} errors")
        s = section("Test")
        row(s, "Timestamp", str(r.get("timestamp")))
        row(s, "Duration", fmt(r.get("duration_s"), 0, "s"))
        row(s, "Total data used", fmt(r.get("data_used_mb"), 0, "MB"))
        self.tree.expandAll()

    # ------------------------------------------------------------------ history
    def refresh_history(self):
        rows = self.history.rows()
        t = self.hist_table
        t.setRowCount(len(rows))
        for i, rw in enumerate(reversed(rows)):
            vals = [rw["id"], (rw["timestamp"] or "").replace("T", " "), fmt(rw["down"]), fmt(rw["up"]),
                    fmt(rw["ping"]), fmt(rw["jitter"]), fmt(rw["loss"]), rw["grade"], rw["medium"], rw["server"]]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if j == 0:
                    item.setData(Qt.ItemDataRole.UserRole, rw["id"])
                t.setItem(i, j, item)
        t.resizeColumnsToContents()
        self.hist_chart.clear()
        self.hist_chart.x_ticks = [(rw["timestamp"] or "")[5:16].replace("T", " ") for rw in rows] or None
        self.hist_chart.set_series("download", DL, [(i, rw["down"]) for i, rw in enumerate(rows)])
        self.hist_chart.set_series("upload", UL, [(i, rw["up"]) for i, rw in enumerate(rows)])
        self.hist_chart.set_series("ping ms", IDLE, [(i, rw["ping"]) for i, rw in enumerate(rows)])

    def _selected_id(self):
        row = self.hist_table.currentRow()
        if row < 0:
            return None
        return self.hist_table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def open_history(self, *_):
        rid = self._selected_id()
        if rid is None:
            return
        r = self.history.raw(rid)
        if r:
            self.result = r
            self.show_result(r)
            self.export_btn.setEnabled(True)
            self.tabs.setCurrentIndex(0)

    def delete_history(self):
        rid = self._selected_id()
        if rid is not None and QMessageBox.question(self, "Delete", f"Delete result #{rid}?") == \
                QMessageBox.StandardButton.Yes:
            self.history.delete(rid)
            self.refresh_history()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export history", "netquality_history.csv", "CSV (*.csv)")
        if path:
            self.history.export_csv(path)

    def export_json(self):
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export result", "netquality_result.json", "JSON (*.json)")
        if path:
            out = dict(self.result, diagnosis=diagnosis.diagnose(self.result),
                       activities=diagnosis.activities(self.result))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)

    def closeEvent(self, e):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("NetQuality")
    app.setStyleSheet(STYLE)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
