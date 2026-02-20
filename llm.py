"""OpenAI gpt-4o-mini integration for replacement car selection."""

import json
import os

from openai import OpenAI
from pydantic import BaseModel, ValidationError


# ── Response schema ────────────────────────────────────────────────────────────

class CarSelection(BaseModel):
    car_id: int
    reasoning: str


class OrderDecision(BaseModel):
    selections: list[CarSelection]
    strategy: str


# ── Prompt helpers ─────────────────────────────────────────────────────────────

def _sold_table(sold_cars: list[dict]) -> str:
    lines = ["make | model | year | condition | min_price | sale_date"]
    lines.append("-" * 72)
    for c in sold_cars:
        lines.append(
            f"{c['make']} | {c['model']} | {c['year']} | {c['condition']}"
            f" | ${float(c['min_price'] or 0):,.0f} | {c.get('sale_date', '')}"
        )
    return "\n".join(lines)


def _candidates_table(candidates: list[dict]) -> str:
    lines = ["car_id | make | model | year | condition | min_price"]
    lines.append("-" * 72)
    for c in candidates:
        lines.append(
            f"{c['id']} | {c['make']} | {c['model']} | {c['year']}"
            f" | {c['condition']} | ${float(c['min_price'] or 0):,.0f}"
        )
    return "\n".join(lines)


# ── Main function ──────────────────────────────────────────────────────────────

def select_replacement_cars(
    sold_cars: list[dict],
    candidates: list[dict],
    min_order: int,
    max_order: int,
) -> OrderDecision:
    """
    Call OpenAI gpt-4o-mini to select replacement vehicles.

    The model receives:
      - A table of cars sold since the last order
      - A table of available candidates from the market pool
      - Ordering bounds [min_order, max_order]

    Returns an OrderDecision with selections clamped to [min_order, max_order].
    Raises on API failure or unparseable response.
    """
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    system_prompt = (
        "You are the inventory manager at Main Street Motors, a car dealership. "
        "Your job is to replenish inventory by selecting replacement vehicles from "
        "the available market pool after sales occur.\n\n"
        "Rules:\n"
        f"  - You must select between {min_order} and {max_order} cars (inclusive).\n"
        "  - Prefer cars that closely match the make, model, year, condition, and "
        "price point of the vehicles that were sold.\n"
        "  - Maintain a healthy variety of makes, models, conditions, and price ranges.\n"
        "  - Only select cars using their exact car_id from the candidates table.\n\n"
        "Respond with ONLY a JSON object matching this exact schema — no extra text:\n"
        "{\n"
        '  "selections": [\n'
        '    {"car_id": <integer>, "reasoning": "<brief reason>"},\n'
        "    ...\n"
        "  ],\n"
        '  "strategy": "<one sentence describing your overall purchasing strategy>"\n'
        "}"
    )

    user_prompt = (
        f"## Cars sold since last order ({len(sold_cars)} total):\n"
        f"{_sold_table(sold_cars)}\n\n"
        f"## Available candidates from market pool ({len(candidates)} cars):\n"
        f"{_candidates_table(candidates)}\n\n"
        f"Select between {min_order} and {max_order} vehicles to purchase as "
        "replacement inventory. Briefly explain your overall strategy."
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        temperature=0.3,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )

    raw = response.choices[0].message.content.strip()
    decision = OrderDecision.model_validate_json(raw)

    # Deduplicate by car_id (LLM can occasionally repeat an ID)
    seen: set[int] = set()
    unique: list[CarSelection] = []
    for sel in decision.selections:
        if sel.car_id not in seen:
            seen.add(sel.car_id)
            unique.append(sel)
    decision.selections = unique

    # Clamp selections to [min_order, max_order]
    if len(decision.selections) > max_order:
        decision.selections = decision.selections[:max_order]
    elif len(decision.selections) < min_order:
        selected_ids = {s.car_id for s in decision.selections}
        extras = [c for c in candidates if c["id"] not in selected_ids]
        for c in extras[: min_order - len(decision.selections)]:
            decision.selections.append(
                CarSelection(
                    car_id=c["id"],
                    reasoning="Auto-added to meet minimum order requirement.",
                )
            )

    return decision
