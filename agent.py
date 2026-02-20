"""
agent.py — Main Street Motors Inventory Replenishment Agent

Polls simulation_state.json every few seconds. Every 3 simulation days it
checks how many cars have been sold since the last order. If more than 1 car
has accumulated, it calls OpenAI gpt-4o-mini to select replacement vehicles,
adds them to DMS inventory, and records the debit in the ERP.

USAGE
─────
  # One-time setup
  bash setup.sh

  # Run (simulation must be running in another terminal)
  .venv/bin/python agent.py

  # Dry-run: log decisions without writing to any database
  .venv/bin/python agent.py --dry-run

  # Custom options
  .venv/bin/python agent.py --poll-interval 5 --db-dir /path/to/data
"""

import argparse
import json
import logging
import os
import time
from datetime import date
from typing import Optional

from dotenv import load_dotenv

from inventory import get_sold_cars_since, get_candidate_pool, purchase_car
from llm import select_replacement_cars
from state import AgentState, load_state, save_state

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────

DEFAULT_DB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "auto-dealership", "data")
)
STATE_FILE = os.path.join(os.path.dirname(__file__), "agent_state.json")
LOG_FILE   = os.path.join(os.path.dirname(__file__), "agent_log.txt")

# ── Config ─────────────────────────────────────────────────────────────────────

CYCLE_DAYS        = 3   # run ordering check every N simulation days
MIN_SALES_TO_ORDER = 2  # trigger an order when this many cars have sold


# ── Logging ────────────────────────────────────────────────────────────────────

def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("newcar_agent")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(LOG_FILE)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = _setup_logging()


# ── Agent ──────────────────────────────────────────────────────────────────────

class InventoryAgent:
    """
    Runs one ordering cycle every CYCLE_DAYS simulation days.

    Accumulation: last_order_date advances only when cars are purchased, so
    sales from consecutive windows are counted together until the threshold
    is met.
    """

    def __init__(self, db_dir: str, state_path: str, dry_run: bool = False) -> None:
        self.db_dir     = db_dir
        self.state_path = state_path
        self.dry_run    = dry_run
        self.state: AgentState = load_state(state_path)

    # ── Entry point called on every sim date change ───────────────────────────

    def on_date_change(self, current_date: str, sim_start_date: str) -> None:
        """Called once each time current_date advances in simulation_state.json."""

        # --- Detect a new simulation run and reset state ---
        stale_cursor = (
            self.state.last_cycle_date is not None
            and self.state.last_cycle_date > current_date
        )
        if self.state.sim_start_date != sim_start_date or stale_cursor:
            if self.state.sim_start_date is None:
                log.info(f"Agent initializing. Simulation start_date: {sim_start_date}")
            elif stale_cursor:
                log.info(
                    f"Stale state detected (last_cycle_date {self.state.last_cycle_date} "
                    f"is ahead of current_date {current_date}). Resetting agent state."
                )
            else:
                log.info(
                    f"New simulation detected "
                    f"(start_date changed: {self.state.sim_start_date} → {sim_start_date}). "
                    f"Resetting agent state."
                )

            self.state = AgentState(
                sim_start_date=sim_start_date,
                last_cycle_date=sim_start_date,
                last_order_date=sim_start_date,
            )
            save_state(self.state, self.state_path)

        # --- Check if CYCLE_DAYS have elapsed since the last cycle ---
        last_cycle = date.fromisoformat(self.state.last_cycle_date)
        today      = date.fromisoformat(current_date)
        days_elapsed = (today - last_cycle).days

        if days_elapsed < CYCLE_DAYS:
            log.info(
                f"{current_date}: {days_elapsed} day(s) since last cycle — "
                f"next cycle in {CYCLE_DAYS - days_elapsed} day(s)."
            )
            return

        # --- Run the ordering cycle ---
        log.info("=" * 60)
        log.info(
            f"Inventory cycle — {current_date} "
            f"({days_elapsed} days since last cycle)"
        )
        self._run_ordering_cycle(current_date)

    # ── Ordering cycle ────────────────────────────────────────────────────────

    def _run_ordering_cycle(self, current_date: str) -> None:
        since      = self.state.last_order_date
        sold_cars  = get_sold_cars_since(since_date=since, db_dir=self.db_dir)
        sold_count = len(sold_cars)

        log.info(f"Cars sold since last order ({since}): {sold_count}")

        if sold_count < MIN_SALES_TO_ORDER:
            log.info(
                f"Only {sold_count} sold — need at least {MIN_SALES_TO_ORDER} "
                f"to trigger an order. Accumulating."
            )
            self.state.last_cycle_date = current_date
            save_state(self.state, self.state_path)
            return

        # --- Build candidate pool ---
        candidates = get_candidate_pool(sold_cars, db_dir=self.db_dir)

        if not candidates:
            log.warning("No available cars in market pool. Skipping order this cycle.")
            self.state.last_cycle_date = current_date
            save_state(self.state, self.state_path)
            return

        log.info(f"Candidate pool: {len(candidates)} cars available")

        # --- Compute order quantity bounds ---
        min_order = max(1, sold_count - 2)
        max_order = min(sold_count + 2, len(candidates))
        log.info(f"Order bounds: [{min_order}, {max_order}]")

        # --- Call OpenAI ---
        log.info("Calling OpenAI gpt-4o-mini for replacement selection...")
        try:
            decision = select_replacement_cars(
                sold_cars, candidates, min_order, max_order
            )
        except Exception as exc:
            log.error(f"OpenAI call failed: {exc}. Will retry in {CYCLE_DAYS} days.")
            # Advance last_cycle_date so we back off and retry at the next 3-day window
            self.state.last_cycle_date = current_date
            save_state(self.state, self.state_path)
            return

        log.info(f"Strategy: {decision.strategy}")
        log.info(f"Selected {len(decision.selections)} car(s) to order")

        # --- Purchase selected cars ---
        candidate_map = {c["id"]: c for c in candidates}
        purchased  = []
        total_cost = 0.0

        for sel in decision.selections:
            car = candidate_map.get(sel.car_id)
            if car is None:
                log.warning(f"car_id {sel.car_id} not found in candidate pool — skipping")
                continue

            cost = float(car["min_price"] or 0)
            total_cost += cost

            if self.dry_run:
                log.info(
                    f"  [DRY-RUN] {car['year']} {car['make']} {car['model']}"
                    f" ({car['condition']}) VIN:{car['vin']} ${cost:,.0f}"
                    f" — {sel.reasoning}"
                )
            else:
                purchase_car(car, current_date=current_date, db_dir=self.db_dir)
                log.info(
                    f"  Purchased: {car['year']} {car['make']} {car['model']}"
                    f" ({car['condition']}) VIN:{car['vin']} ${cost:,.0f}"
                    f" — {sel.reasoning}"
                )
            purchased.append(car)

        n_purchased = len(purchased)
        tag = "[DRY-RUN] " if self.dry_run else ""
        log.info(
            f"{tag}Cycle complete: {n_purchased} car(s) ordered | "
            f"{sold_count} sale(s) triggered | Total cost: ${total_cost:,.0f}"
        )

        # --- Advance state ---
        if not self.dry_run and n_purchased > 0:
            self.state.last_order_date = current_date
        self.state.last_cycle_date = current_date
        save_state(self.state, self.state_path)


# ── Polling loop ───────────────────────────────────────────────────────────────

def run_agent(agent: InventoryAgent, poll_interval: float, db_dir: str) -> None:
    sim_state_path = os.path.join(db_dir, "simulation_state.json")

    log.info(f"Inventory agent started — polling every {poll_interval}s")
    log.info(f"State file : {agent.state_path}")
    log.info(f"DB dir     : {db_dir}")
    log.info(f"State file : {agent.state_path}")
    if agent.dry_run:
        log.info("[DRY-RUN] No database writes will be made.")

    last_seen_date: Optional[str] = None

    try:
        while True:
            # --- Read simulation state ---
            try:
                with open(sim_state_path) as f:
                    sim_state = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(poll_interval)
                continue

            status         = sim_state.get("status")
            current_date   = sim_state.get("current_date")
            sim_start_date = sim_state.get("start_date")

            if status == "completed":
                log.info("Simulation complete — agent shutting down.")
                break

            # --- Fire on date change ---
            if current_date and current_date != last_seen_date:
                log.info(f"Date change detected: {last_seen_date} → {current_date}")
                agent.on_date_change(current_date, sim_start_date)
                last_seen_date = current_date

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        log.info("Agent stopped by user.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Main Street Motors — Inventory Replenishment Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--poll-interval", type=float, default=3.0,
        help="Seconds between checks of simulation_state.json (default: 3)",
    )
    p.add_argument(
        "--db-dir", default=None,
        help=f"DB directory (default: $DB_DIR or {DEFAULT_DB_DIR})",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Log decisions without writing to any database",
    )
    return p


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    db_dir = args.db_dir or os.getenv("DB_DIR", DEFAULT_DB_DIR)

    agent = InventoryAgent(
        db_dir=db_dir,
        state_path=STATE_FILE,
        dry_run=args.dry_run,
    )
    run_agent(agent, poll_interval=args.poll_interval, db_dir=db_dir)


if __name__ == "__main__":
    main()
