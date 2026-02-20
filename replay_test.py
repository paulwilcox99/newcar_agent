"""
replay_test.py — Feed simulation dates to the agent at human speed.

Replays the already-completed simulation date-by-date by writing
simulation_state.json one day at a time, allowing the agent to process
each day normally. Uses the existing populated databases — no DB reset needed.

Usage:
    # Terminal 1 — start the agent
    .venv/bin/python agent.py --dry-run

    # Terminal 2 — run the replayer
    .venv/bin/python replay_test.py
"""

import json
import os
import time
from datetime import date, timedelta

DB_DIR     = os.path.abspath("../auto-dealership/data")
STATE_PATH = os.path.join(DB_DIR, "simulation_state.json")

START_DATE = date(2026, 2, 20)
END_DATE   = date(2026, 3, 6)    # inclusive last day
DAY_DELAY  = 2.0                 # seconds between each simulated day


def write_state(current: date, status: str = "running") -> None:
    state = {
        "status":       status,
        "current_date": current.isoformat(),
        "start_date":   START_DATE.isoformat(),
        "end_date":     END_DATE.isoformat(),
    }
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def main() -> None:
    print(f"Replaying {START_DATE} → {END_DATE}  ({DAY_DELAY}s per day)")
    print(f"State file: {STATE_PATH}\n")

    current = START_DATE
    while current <= END_DATE:
        print(f"  Advancing to {current.isoformat()} ...")
        write_state(current)
        time.sleep(DAY_DELAY)
        current += timedelta(days=1)

    print("\nReplay complete — writing 'completed' status.")
    write_state(END_DATE, status="completed")


if __name__ == "__main__":
    main()
