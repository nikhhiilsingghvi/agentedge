"""
AgentEdge Logger
================
Structured, rotating file logger with rich console output.
Every agent event is timestamped and stored for paper analysis.

Patent-pending architecture: Resource-Adaptive Task Decomposition
Copyright (c) 2026 AgentEdge Project, DSCE ECE Dept.
"""

import logging
import os
import json
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# ─── RICH CONSOLE THEME ──────────────────────────────────────────────────────
custom_theme = Theme({
    "info":     "cyan",
    "warning":  "yellow bold",
    "error":    "red bold",
    "critical": "red bold reverse",
    "success":  "green bold",
    "agent":    "magenta bold",
    "sensor":   "blue",
    "actuator": "bright_yellow",
    "resource": "bright_cyan",
    "bench":    "bright_green",
})

console = Console(theme=custom_theme)


def get_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    """
    Returns a configured logger with:
    - Rich console handler (colored, readable)
    - Rotating file handler (JSON-structured for analysis)
    """
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Already configured

    logger.setLevel(logging.DEBUG)

    # ── Console handler (Rich) ────────────────────────────────────────────────
    console_handler = RichHandler(
        console=Console(theme=custom_theme),
        show_time=True,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    console_handler.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)

    # ── File handler (Rotating, structured) ──────────────────────────────────
    log_file = os.path.join(log_dir, f"{name.replace('.', '_')}.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(file_handler)

    return logger


class AgentEventLogger:
    """
    Structured event logger for agent loop events.
    All events written to JSON log for benchmark analysis.
    This is the data source for paper's experimental results.
    """

    def __init__(self, session_id: str, log_dir: str = "logs"):
        self.session_id = session_id
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.event_file = os.path.join(
            log_dir, f"session_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        )
        self._events: list = []
        self.logger = get_logger(f"AgentEvent.{session_id}", log_dir)

    def log_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        latency_ms: Optional[float] = None,
        ram_percent: Optional[float] = None,
        energy_mj: Optional[float] = None,
    ) -> None:
        """Log a structured agent event with all measurement fields."""
        event = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "session_id": self.session_id,
            "event_type": event_type,
            "data": data,
            "measurements": {
                "latency_ms": round(latency_ms, 3) if latency_ms else None,
                "ram_percent": round(ram_percent, 2) if ram_percent else None,
                "energy_mj": round(energy_mj, 4) if energy_mj else None,
            },
        }
        self._events.append(event)

        # Append to JSONL file
        with open(self.event_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def get_session_summary(self) -> Dict[str, Any]:
        """Returns summary statistics for this session — used in benchmark."""
        if not self._events:
            return {}

        latencies = [
            e["measurements"]["latency_ms"]
            for e in self._events
            if e["measurements"]["latency_ms"] is not None
        ]
        rams = [
            e["measurements"]["ram_percent"]
            for e in self._events
            if e["measurements"]["ram_percent"] is not None
        ]
        energies = [
            e["measurements"]["energy_mj"]
            for e in self._events
            if e["measurements"]["energy_mj"] is not None
        ]

        return {
            "session_id": self.session_id,
            "total_events": len(self._events),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "max_latency_ms": round(max(latencies), 2) if latencies else 0,
            "avg_ram_percent": round(sum(rams) / len(rams), 2) if rams else 0,
            "peak_ram_percent": round(max(rams), 2) if rams else 0,
            "total_energy_mj": round(sum(energies), 4) if energies else 0,
            "event_log_file": self.event_file,
        }
