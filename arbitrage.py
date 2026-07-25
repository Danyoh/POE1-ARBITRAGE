"""Arbitrage detection logic for mixed chaos/divine listings."""

from __future__ import annotations

from bisect import bisect_left
from typing import Any


def normalize_currency(currency: str) -> str:
    c = (currency or "").strip().lower()
    if c in {"chaos", "chaos orb", "chaosorb", "c"}:
        return "chaos"
    if c in {"divine", "divine orb", "divineorb", "div"}:
        return "divine"
    return c


def get_item_name(record: dict[str, Any]) -> str:
    item = record.get("item", {})
    name = (item.get("name") or "").strip()
    type_line = (item.get("typeLine") or "").strip()
    if name and type_line:
        return f"{name} {type_line}".strip()
    return name or type_line or "Unknown Item"


def get_item_key(record: dict[str, Any]) -> str:
    item = record.get("item", {})
    name = (item.get("name") or "").strip().lower()
    type_line = (item.get("typeLine") or "").strip().lower()
    ilvl = str(item.get("ilvl", ""))
    corrupted = str(item.get("corrupted", False))
    return "|".join([name, type_line, ilvl, corrupted])


def extract_price(record: dict[str, Any], divine_to_chaos: float) -> dict[str, Any] | None:
    listing = record.get("listing", {})
    price = listing.get("price")
    if not isinstance(price, dict):
        return None

    currency = normalize_currency(str(price.get("currency", "")))
    amount = price.get("amount")
    if amount is None:
        return None

    try:
        amount_f = float(amount)
    except (TypeError, ValueError):
        return None

    if currency == "chaos":
        chaos_equivalent = amount_f
    elif currency == "divine":
        chaos_equivalent = amount_f * divine_to_chaos
    else:
        return None

    return {
        "currency": currency,
        "amount": amount_f,
        "chaos_equivalent": chaos_equivalent,
    }


def _default_group(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_name": get_item_name(record),
        "chaos_listings": [],
        "divine_listings": [],
    }


def _update_group_with_record(
    group: dict[str, Any], record: dict[str, Any], divine_to_chaos: float
) -> None:
    price = extract_price(record, divine_to_chaos)
    if price is None:
        return

    listing_id = str(record.get("id", ""))
    if price["currency"] == "chaos":
        group["chaos_listings"].append({**price, "listing_id": listing_id})
    elif price["currency"] == "divine":
        # We can only execute this strategy when buy side is >= 1 divine.
        if price["amount"] >= 1.0:
            group["divine_listings"].append({**price, "listing_id": listing_id})


def _best_threshold_match(
    chaos_listings: list[dict[str, Any]],
    divine_listings: list[dict[str, Any]],
    min_profit_chaos: float,
) -> dict[str, Any] | None:
    if not chaos_listings or not divine_listings:
        return None

    # Sort chaos asks ascending so we can quickly find the first listing
    # at/above each divine+profit threshold.
    sorted_chaos = sorted(chaos_listings, key=lambda x: x["amount"])
    chaos_amounts = [row["amount"] for row in sorted_chaos]

    best_match: dict[str, Any] | None = None
    for divine in divine_listings:
        required_chaos = divine["chaos_equivalent"] + min_profit_chaos
        idx = bisect_left(chaos_amounts, required_chaos)
        if idx >= len(sorted_chaos):
            continue

        matched_chaos = sorted_chaos[idx]
        profit = matched_chaos["amount"] - divine["chaos_equivalent"]
        candidate = {
            "profit_chaos": round(profit, 2),
            "buy_divine_amount": divine["amount"],
            "buy_chaos_equivalent": round(divine["chaos_equivalent"], 2),
            "sell_chaos_amount": matched_chaos["amount"],
            "divine_listing_id": divine["listing_id"],
            "chaos_listing_id": matched_chaos["listing_id"],
        }
        if best_match is None or candidate["profit_chaos"] > best_match["profit_chaos"]:
            best_match = candidate

    return best_match


def analyze_arbitrage(
    records: list[dict[str, Any]],
    divine_to_chaos: float,
    min_profit_chaos: float,
) -> list[dict[str, Any]]:
    if divine_to_chaos <= 0:
        return []

    grouped: dict[str, dict[str, Any]] = {}

    for record in records:
        key = get_item_key(record)
        group = grouped.setdefault(key, _default_group(record))
        _update_group_with_record(group, record, divine_to_chaos)

    opportunities: list[dict[str, Any]] = []
    for group in grouped.values():
        match = _best_threshold_match(
            group["chaos_listings"],
            group["divine_listings"],
            min_profit_chaos,
        )
        if not match:
            continue

        opportunities.append(
            {
                "item": group["item_name"],
                **match,
            }
        )

    opportunities.sort(key=lambda x: x["profit_chaos"], reverse=True)
    return opportunities
