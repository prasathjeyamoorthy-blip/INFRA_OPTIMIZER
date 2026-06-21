"""
dashboard.py — PyQt6 GUI dashboard for the Cost Optimizer Agent.

API calls run in a background QThread so the UI never blocks.
Polls every 3 seconds. Three tabs:
  Tab 1 — Instances  : live EC2 state table with metrics
  Tab 2 — Cycles     : full LLM decision history
  Tab 3 — Metrics    : live CPU + Latency charts + cost cards

Usage:
    python Agent/dashboard.py
"""

import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx
import pyqtgraph as pg
from dotenv import load_dotenv
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

API_BASE    = os.getenv("API_BASE_URL", "http://localhost:8000")
POLL_MS     = 3000
MAX_HISTORY = 20

CHART_COLORS = [
    "#2ecc71", "#3498db", "#e67e22", "#9b59b6",
    "#1abc9c", "#e74c3c", "#f39c12", "#00bcd4",
]

DARK_BG    = "#1e1e2e"
PANEL_BG   = "#181825"
HEADER_BG  = "#313244"
TEXT_COLOR = "#cdd6f4"
MUTED      = "#a6adc8"
GREEN      = "#2ecc71"
RED        = "#e74c3c"
ORANGE     = "#e67e22"

# ---------------------------------------------------------------------------
# Background worker thread — all httpx calls happen here
# ---------------------------------------------------------------------------

class PollerThread(QThread):
    data_ready = pyqtSignal(dict)   # emits combined payload
    error      = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            try:
                with httpx.Client(timeout=5.0) as client:
                    def get(path):
                        try:
                            r = client.get(f"{API_BASE}{path}")
                            r.raise_for_status()
                            return r.json()
                        except Exception:
                            return None

                    instances = get("/api/instances")
                    cycles    = get("/api/cycles?limit=50")
                    cost      = get("/api/cost")
                    ks        = get("/api/killswitch")

                if instances is None or cost is None or ks is None:
                    self.error.emit(f"Cannot reach {API_BASE}")
                else:
                    self.data_ready.emit({
                        "instances": instances,
                        "cycles":    cycles or [],
                        "cost":      cost,
                        "ks":        ks,
                    })
            except Exception as exc:
                self.error.emit(str(exc))

            self.msleep(POLL_MS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_ts(ts_str) -> str:
    if not ts_str:
        return "—"
    try:
        return datetime.fromisoformat(str(ts_str)).strftime("%H:%M:%S")
    except Exception:
        return str(ts_str)[:19]


def _item(text: str, color: str | None = None, bold: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if color:
        it.setForeground(QColor(color))
    if bold:
        f = it.font(); f.setBold(True); it.setFont(f)
    return it


TABLE_STYLE = f"""
    QTableWidget {{
        background: {DARK_BG}; color: {TEXT_COLOR};
        gridline-color: {HEADER_BG}; border: none;
    }}
    QHeaderView::section {{
        background: {HEADER_BG}; color: {TEXT_COLOR};
        font-weight: bold; padding: 4px; border: none;
    }}
    QTableWidget::item:alternate {{ background: {PANEL_BG}; }}
    QTableWidget::item:selected  {{ background: #1f538d; }}
"""


def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setStyleSheet(TABLE_STYLE)
    t.setSortingEnabled(False)
    return t


# ---------------------------------------------------------------------------
# Tab 1 — Instances
# ---------------------------------------------------------------------------

class InstancesTab(QWidget):
    HEADERS = ["Name", "Type", "Status", "Protected",
               "Last Action", "CPU % (last 3)", "Latency ms (last 3)", "Updated"]

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.table = _make_table(self.HEADERS)
        layout.addWidget(self.table)

    def refresh(self, instances: list):
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(instances))
        for r, inst in enumerate(instances):
            tags      = inst.get("tags", {})
            protected = tags.get("protected", "—") if isinstance(tags, dict) else "—"
            status    = inst.get("status", "")
            metrics   = inst.get("metrics", {})
            cpu_vals  = metrics.get("CpuUtilizationPercent", [])
            lat_vals  = metrics.get("LatencyMs", [])
            s_color   = GREEN if status == "running" else RED

            row = [
                inst.get("name", ""),
                inst.get("instance_type", ""),
                status,
                protected,
                inst.get("last_action") or "—",
                ", ".join(f"{v:.1f}" for v in cpu_vals) or "—",
                ", ".join(f"{v:.1f}" for v in lat_vals) or "—",
                _fmt_ts(inst.get("updated_at", "")),
            ]
            for c, val in enumerate(row):
                self.table.setItem(r, c, _item(val, s_color if c == 2 else None))
        self.table.setUpdatesEnabled(True)


# ---------------------------------------------------------------------------
# Tab 2 — Cycles
# ---------------------------------------------------------------------------

class CyclesTab(QWidget):
    HEADERS = ["Cycle #", "Timestamp", "Instance", "Action",
               "Reasoning", "Validated", "AWS Response"]

    _ACOLORS = {
        "stop_instance":   RED,
        "start_instance":  GREEN,
        "resize_instance": "#f39c12",
        "tag_instance":    "#3498db",
        "do_nothing":      MUTED,
        "alert_human":     ORANGE,
    }

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.table = _make_table(self.HEADERS)
        layout.addWidget(self.table)

    def refresh(self, cycles: list):
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(cycles))
        for r, cyc in enumerate(cycles):
            action = cyc.get("action", "")
            ac     = self._ACOLORS.get(action, TEXT_COLOR)
            vc     = GREEN if cyc.get("validated") else RED
            row    = [
                str(cyc.get("cycle", "")),
                _fmt_ts(cyc.get("timestamp", "")),
                cyc.get("instance_name", ""),
                action,
                cyc.get("reasoning", ""),
                "✓" if cyc.get("validated") else "✗",
                cyc.get("aws_response", ""),
            ]
            colors = [None, None, None, ac, None, vc, None]
            for c, (val, col) in enumerate(zip(row, colors)):
                self.table.setItem(r, c, _item(val, col))
        self.table.setUpdatesEnabled(True)


# ---------------------------------------------------------------------------
# Tab 3 — Metrics & Cost
# ---------------------------------------------------------------------------

class MetricsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Cost cards
        cards_box = QGroupBox("💰  Cost Summary")
        cards_box.setStyleSheet(
            f"QGroupBox {{ color:{TEXT_COLOR}; font-weight:bold; font-size:13px;"
            f"border:1px solid {HEADER_BG}; border-radius:6px;"
            f"margin-top:6px; padding-top:6px; }}")
        cl = QHBoxLayout(cards_box)
        self._c_total   = self._card("Total Instances", "—")
        self._c_running = self._card("Running",         "—", GREEN)
        self._c_stopped = self._card("Stopped",         "—", RED)
        self._c_hourly  = self._card("Est. Hourly USD", "—", "#f39c12")
        for c in (self._c_total, self._c_running, self._c_stopped, self._c_hourly):
            cl.addWidget(c)
        layout.addWidget(cards_box)

        # Charts
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._cpu_plot = self._make_plot("CPU Utilization %", "Samples", "%")
        self._lat_plot = self._make_plot("Latency ms",         "Samples", "ms")
        splitter.addWidget(self._cpu_plot)
        splitter.addWidget(self._lat_plot)
        layout.addWidget(splitter, stretch=1)

        self._cpu_hist: dict[str, list] = defaultdict(list)
        self._lat_hist: dict[str, list] = defaultdict(list)
        self._cpu_lines: dict[str, pg.PlotDataItem] = {}
        self._lat_lines: dict[str, pg.PlotDataItem] = {}
        self._colors: dict[str, str] = {}

    # ---- helpers ----

    def _card(self, label, value, val_color=TEXT_COLOR):
        box = QWidget()
        box.setStyleSheet(f"background:{HEADER_BG}; border-radius:8px;")
        vl  = QVBoxLayout(box)
        vl.setContentsMargins(12, 8, 12, 8)
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color:{MUTED}; font-size:11px; background:transparent;")
        val = QLabel(value)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setStyleSheet(
            f"color:{val_color}; font-size:26px; font-weight:bold; background:transparent;")
        val.setObjectName("val")
        vl.addWidget(lbl)
        vl.addWidget(val)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return box

    def _set_card(self, card, text):
        card.findChild(QLabel, "val").setText(text)

    def _make_plot(self, title, x_lbl, y_lbl):
        pw = pg.PlotWidget()
        pw.setBackground(DARK_BG)
        pw.setTitle(title, color=TEXT_COLOR, size="11pt")
        pw.showGrid(x=True, y=True, alpha=0.25)
        pw.setLabel("left",   y_lbl, color=TEXT_COLOR)
        pw.setLabel("bottom", x_lbl, color=TEXT_COLOR)
        pw.addLegend(offset=(10, 10))
        pw.getAxis("left").setTextPen(TEXT_COLOR)
        pw.getAxis("bottom").setTextPen(TEXT_COLOR)
        return pw

    def _color_for(self, name):
        if name not in self._colors:
            self._colors[name] = CHART_COLORS[len(self._colors) % len(CHART_COLORS)]
        return self._colors[name]

    # ---- refresh ----

    def refresh_cost(self, data):
        self._set_card(self._c_total,   str(data.get("total_instances", "—")))
        self._set_card(self._c_running, str(data.get("running_count",   "—")))
        self._set_card(self._c_stopped, str(data.get("stopped_count",   "—")))
        self._set_card(self._c_hourly,  f"${data.get('estimated_hourly_usd', 0.0):.4f}")

    def refresh_charts(self, instances):
        for inst in instances:
            name    = inst.get("name") or inst.get("id", "?")
            metrics = inst.get("metrics", {})
            cpu_pts = metrics.get("CpuUtilizationPercent", [])
            lat_pts = metrics.get("LatencyMs", [])

            if cpu_pts:
                h = self._cpu_hist[name]
                h.append(cpu_pts[-1])
                if len(h) > MAX_HISTORY: h.pop(0)

            if lat_pts:
                h = self._lat_hist[name]
                h.append(lat_pts[-1])
                if len(h) > MAX_HISTORY: h.pop(0)

        for name, vals in self._cpu_hist.items():
            x = list(range(len(vals)))
            if name not in self._cpu_lines:
                pen = pg.mkPen(self._color_for(name), width=2)
                self._cpu_lines[name] = self._cpu_plot.plot(x, vals, pen=pen, name=name)
            else:
                self._cpu_lines[name].setData(x, vals)

        for name, vals in self._lat_hist.items():
            x = list(range(len(vals)))
            if name not in self._lat_lines:
                pen = pg.mkPen(self._color_for(name), width=2)
                self._lat_lines[name] = self._lat_plot.plot(x, vals, pen=pen, name=name)
            else:
                self._lat_lines[name].setData(x, vals)


# ---------------------------------------------------------------------------
# Kill Switch widget
# ---------------------------------------------------------------------------

class KillSwitchWidget(QGroupBox):
    def __init__(self):
        super().__init__("🔒  Kill Switch")
        self.setStyleSheet(
            f"QGroupBox {{ color:{TEXT_COLOR}; font-weight:bold;"
            f"border:1px solid {HEADER_BG}; border-radius:6px;"
            f"margin-top:6px; padding:4px 8px; }}")
        self._active = False
        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 4, 8, 4)
        self._lbl = QLabel("○  Inactive")
        self._lbl.setStyleSheet(f"color:{GREEN}; font-weight:bold; min-width:130px;")
        self._btn = QPushButton("Activate")
        self._btn.setFixedWidth(110)
        self._btn.setStyleSheet(
            f"QPushButton{{background:{ORANGE};color:white;border-radius:4px;padding:4px 8px;}}"
            f"QPushButton:hover{{background:#d35400;}}")
        self._btn.clicked.connect(self._toggle)
        hl.addWidget(self._lbl)
        hl.addWidget(self._btn)

    def refresh(self, active: bool):
        self._active = active
        if active:
            self._lbl.setText("●  ACTIVE — paused")
            self._lbl.setStyleSheet(f"color:{RED}; font-weight:bold; min-width:130px;")
            self._btn.setText("Deactivate")
            self._btn.setStyleSheet(
                f"QPushButton{{background:{GREEN};color:white;border-radius:4px;padding:4px 8px;}}"
                f"QPushButton:hover{{background:#1e8449;}}")
        else:
            self._lbl.setText("○  Inactive")
            self._lbl.setStyleSheet(f"color:{GREEN}; font-weight:bold; min-width:130px;")
            self._btn.setText("Activate")
            self._btn.setStyleSheet(
                f"QPushButton{{background:{ORANGE};color:white;border-radius:4px;padding:4px 8px;}}"
                f"QPushButton:hover{{background:#d35400;}}")

    def _toggle(self):
        # Run in a tiny thread so the button never freezes
        new_state = not self._active
        self._btn.setEnabled(False)
        t = QThread(self)
        def work():
            try:
                r = httpx.post(
                    f"{API_BASE}/api/killswitch",
                    json={"active": new_state}, timeout=5.0)
                r.raise_for_status()
                result = r.json()
                self.refresh(bool(result.get("active", new_state)))
            except Exception:
                pass
            finally:
                self._btn.setEnabled(True)
        t.run = work
        t.finished.connect(t.deleteLater)
        t.start()


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class CostOptimizerDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cost Optimizer Agent — Live Dashboard")
        self.resize(1400, 820)
        self.setStyleSheet(f"QMainWindow,QWidget{{background:{DARK_BG};}}")

        central = QWidget()
        self.setCentralWidget(central)
        ml = QVBoxLayout(central)
        ml.setContentsMargins(8, 8, 8, 4)
        ml.setSpacing(4)

        # Top bar
        tb = QHBoxLayout()
        title = QLabel("⚡  Cost Optimizer Agent")
        title.setStyleSheet(f"color:{TEXT_COLOR}; font-size:16px; font-weight:bold;")
        tb.addWidget(title)
        tb.addStretch()
        self._ks = KillSwitchWidget()
        tb.addWidget(self._ks)
        ml.addLayout(tb)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane{{border:1px solid {HEADER_BG}; background:{DARK_BG};}}
            QTabBar::tab{{background:{HEADER_BG};color:{MUTED};padding:6px 16px;border-radius:4px;}}
            QTabBar::tab:selected{{background:#1f538d;color:{TEXT_COLOR};font-weight:bold;}}
        """)
        self._inst_tab    = InstancesTab()
        self._cycles_tab  = CyclesTab()
        self._metrics_tab = MetricsTab()
        self._tabs.addTab(self._inst_tab,    "🖥  Instances")
        self._tabs.addTab(self._cycles_tab,  "🔄  Cycles")
        self._tabs.addTab(self._metrics_tab, "📈  Metrics & Cost")
        ml.addWidget(self._tabs)

        # Status bar
        self._sb = QStatusBar()
        self._sb.setStyleSheet(
            f"QStatusBar{{background:{PANEL_BG};color:{MUTED};font-size:11px;}}")
        self.setStatusBar(self._sb)
        self._sb.showMessage("Connecting to API…")

        # Background poller
        self._poller = PollerThread(self)
        self._poller.data_ready.connect(self._on_data)
        self._poller.error.connect(self._on_error)
        self._poller.start()

    def closeEvent(self, event):
        self._poller.stop()
        self._poller.wait(2000)
        super().closeEvent(event)

    # ------------------------------------------------------------------

    def _on_data(self, payload: dict):
        instances = payload["instances"]
        self._inst_tab.refresh(instances)
        self._cycles_tab.refresh(payload["cycles"])
        self._metrics_tab.refresh_cost(payload["cost"])
        self._metrics_tab.refresh_charts(instances)
        self._ks.refresh(bool(payload["ks"].get("active", False)))

        now = datetime.now().strftime("%H:%M:%S")
        self._sb.setStyleSheet(
            f"QStatusBar{{background:{PANEL_BG};color:{MUTED};font-size:11px;}}")
        self._sb.showMessage(
            f"Updated: {now}  |  {len(instances)} instance(s)  |  "
            f"Polling every {POLL_MS//1000}s  |  {API_BASE}")

    def _on_error(self, msg: str):
        self._sb.setStyleSheet(
            f"QStatusBar{{background:{PANEL_BG};color:{RED};font-size:11px;}}")
        self._sb.showMessage(f"⚠  {msg} — is the API server running?")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pg.setConfigOption("background", DARK_BG)
    pg.setConfigOption("foreground", TEXT_COLOR)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = CostOptimizerDashboard()
    window.show()
    sys.exit(app.exec())
