"""Arbitrage detection logic for mixed chaos/divine listings."""

from __future__ import annotations

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
        "best_chaos": None,
        "best_divine": None,
    }


def _update_group_with_record(
    group: dict[str, Any], record: dict[str, Any], divine_to_chaos: float
) -> None:
    price = extract_price(record, divine_to_chaos)
    if price is None:
        return

    listing_id = str(record.get("id", ""))
    if price["currency"] == "chaos":
        best_chaos = group["best_chaos"]
        if best_chaos is None or price["amount"] < best_chaos["amount"]:
            group["best_chaos"] = {**price, "listing_id": listing_id}
    elif price["currency"] == "divine":
        best_divine = group["best_divine"]
        if best_divine is None or price["chaos_equivalent"] < best_divine["chaos_equivalent"]:
            group["best_divine"] = {**price, "listing_id": listing_id}


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
        best_chaos = group["best_chaos"]
        best_divine = group["best_divine"]
        if not best_chaos or not best_divine:
            continue

        profit = best_chaos["amount"] - best_divine["chaos_equivalent"]
        if profit >= min_profit_chaos:
            opportunities.append(
                {
                    "item": group["item_name"],
                    "profit_chaos": round(profit, 2),
                    "buy_divine_amount": best_divine["amount"],
                    "buy_chaos_equivalent": round(best_divine["chaos_equivalent"], 2),
                    "sell_chaos_amount": best_chaos["amount"],
                    "divine_listing_id": best_divine["listing_id"],
                    "chaos_listing_id": best_chaos["listing_id"],
                }
            )

    opportunities.sort(key=lambda x: x["profit_chaos"], reverse=True)
    return opportunities
