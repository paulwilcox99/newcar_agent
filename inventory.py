"""Database reads and writes for inventory replenishment operations."""

import random
import sqlite3
import os


# ── Connection helpers ─────────────────────────────────────────────────────────

def _db_path(name: str, db_dir: str) -> str:
    return os.path.join(db_dir, name)


def query(db_name: str, sql: str, params: tuple = (), db_dir: str = "./data") -> list[dict]:
    """Read-only query against a business database."""
    path = _db_path(db_name, db_dir)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _write_conn(db_name: str, db_dir: str) -> sqlite3.Connection:
    """Open a WAL-mode write connection. Caller must commit() and close()."""
    conn = sqlite3.connect(_db_path(db_name, db_dir))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Reads ──────────────────────────────────────────────────────────────────────

def get_sold_cars_since(since_date: str, db_dir: str) -> list[dict]:
    """
    Return all DMS cars sold strictly after since_date.
    These are the sales that accumulate until the agent places an order.
    """
    return query(
        "dms.db",
        """
        SELECT make, model, year, condition, min_price, vin, sale_date, customer_name
        FROM   dms_cars
        WHERE  status    = 'sold'
          AND  sale_date > ?
        ORDER  BY sale_date ASC
        """,
        (since_date,),
        db_dir=db_dir,
    )


def _get_vins_in_dms(db_dir: str) -> set[str]:
    """Return all VINs already in dms.db (any status) to prevent duplicate purchases."""
    rows = query("dms.db", "SELECT vin FROM dms_cars", db_dir=db_dir)
    return {r["vin"] for r in rows}


def get_candidate_pool(
    sold_cars: list[dict],
    db_dir: str,
    max_candidates: int = 80,
) -> list[dict]:
    """
    Build a candidate pool from cars_available.db for the LLM to choose from.

    Strategy:
      - Exclude VINs already in dms.db (prevents double-ordering)
      - Prioritize cars matching the make of any sold car (up to 60 slots)
      - Fill remaining slots randomly from other makes
      - Cap total at max_candidates to keep LLM prompt size manageable
    """
    dms_vins = _get_vins_in_dms(db_dir)
    sold_makes = {c["make"] for c in sold_cars}

    all_available = query(
        "cars_available.db",
        """
        SELECT id, make, model, year, vin, condition, min_price
        FROM   sim_cars
        WHERE  status = 'available'
        """,
        db_dir=db_dir,
    )

    # Filter out anything already on the lot
    all_available = [c for c in all_available if c["vin"] not in dms_vins]

    same_make = [c for c in all_available if c["make"] in sold_makes]
    other     = [c for c in all_available if c["make"] not in sold_makes]

    # Cap same-make pool to leave room for variety
    if len(same_make) > 60:
        same_make = random.sample(same_make, 60)

    remaining_slots = max_candidates - len(same_make)
    fill = random.sample(other, min(remaining_slots, len(other))) if remaining_slots > 0 and other else []

    return same_make + fill


# ── Writes ─────────────────────────────────────────────────────────────────────

def purchase_car(car: dict, current_date: str, db_dir: str) -> None:
    """
    Record the purchase of a single car across three databases:
      1. dms.db         — add car to lot inventory (status='available')
      2. cars_available.db — mark car as 'used' so it won't be re-selected
      3. erp.db         — record debit transaction for the purchase cost
    """
    conn_dms  = _write_conn("dms.db",          db_dir)
    conn_cars = _write_conn("cars_available.db", db_dir)
    conn_erp  = _write_conn("erp.db",           db_dir)

    try:
        conn_dms.execute(
            """
            INSERT INTO dms_cars
                (make, model, year, vin, condition, min_price, status)
            VALUES (?, ?, ?, ?, ?, ?, 'available')
            """,
            (
                car["make"],
                car["model"],
                car["year"],
                car["vin"],
                car["condition"],
                car["min_price"],
            ),
        )
        conn_dms.commit()

        conn_cars.execute(
            "UPDATE sim_cars SET status = 'used' WHERE id = ?",
            (car["id"],),
        )
        conn_cars.commit()

        conn_erp.execute(
            """
            INSERT INTO erp_transactions
                (transaction_type, amount, payee_payer, description, transaction_date)
            VALUES ('debit', ?, 'Vehicle Acquisition', ?, ?)
            """,
            (
                car["min_price"],
                f"Inventory purchase — {car['year']} {car['make']} {car['model']} VIN:{car['vin']}",
                current_date,
            ),
        )
        conn_erp.commit()

    finally:
        conn_dms.close()
        conn_cars.close()
        conn_erp.close()
