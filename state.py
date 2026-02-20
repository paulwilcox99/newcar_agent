"""Agent state persistence — tracks simulation identity and ordering cursors."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class AgentState:
    # Stored to detect when a new simulation run starts
    sim_start_date: Optional[str] = None
    # Last date the 3-day cycle check ran (advances every cycle)
    last_cycle_date: Optional[str] = None
    # Cursor for "sold since" query — only advances when orders are placed
    last_order_date: Optional[str] = None


def load_state(path: str) -> AgentState:
    """Load state from JSON; return blank defaults if file is missing or corrupt."""
    try:
        with open(path) as f:
            data = json.load(f)
        return AgentState(
            sim_start_date=data.get("sim_start_date"),
            last_cycle_date=data.get("last_cycle_date"),
            last_order_date=data.get("last_order_date"),
        )
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return AgentState()


def save_state(state: AgentState, path: str) -> None:
    """Atomically write state to JSON (write to temp file, then rename)."""
    dir_ = os.path.dirname(os.path.abspath(path))
    with tempfile.NamedTemporaryFile(
        mode="w", dir=dir_, delete=False, suffix=".tmp"
    ) as f:
        json.dump(asdict(state), f, indent=2)
        tmp_path = f.name
    os.replace(tmp_path, path)
