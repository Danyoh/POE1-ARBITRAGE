from __future__ import annotations

from urllib.parse import quote

import streamlit as st

from arbitrage import analyze_arbitrage, normalize_currency
from poe_trade import PoETradeClient, RateLimitError, TradeAPIError


PAGE_SIZE = 10
DEFAULT_MAX_SCAN = 50

COMMON_LEAGUES = [
    "Standard",
    "Hardcore",
    "Allflame",
    "Hardcore Allflame",
]

LEAGUE_ALIASES = {
    "standard": "Standard",
    "hardcore": "Hardcore",
    "allflame": "Allflame",
    "allflame standard": "Allflame",
    "hardcore allflame": "Hardcore Allflame",
    "allflame hardcore": "Hardcore Allflame",
}

FALLBACK_CATEGORY_OPTIONS = [
    ("", "Any"),
    ("weapon", "Any Weapon"),
    ("weapon.one", "One-handed weapon"),
    ("weapon.two", "Two-handed weapon"),
    ("corpse", "Any Corpse"),
    ("accessory", "Accessories"),
    ("armour", "Armour"),
    ("card", "Cards"),
    ("currency", "Currency"),
    ("flask", "Flasks"),
    ("gem", "Gems"),
    ("jewel", "Jewels"),
    ("map", "Maps"),
    ("heistequipment", "Heist Equipment"),
    ("heistmission", "Heist Mission"),
    ("expeditionlogbook", "Expedition Logbooks"),
    ("sanctum", "Sanctum"),
    ("tincture", "Tincture"),
]

RARITY_OPTIONS = [
    ("", "Any"),
    ("normal", "Normal"),
    ("magic", "Magic"),
    ("rare", "Rare"),
    ("unique", "Unique"),
]

TYPE_FILTER_PRESETS = [
    ("", "Any"),
    ("weapon", "Any Weapon"),
    ("weapon.one", "One-handed weapon"),
    ("weapon.two", "Two-handed weapon"),
    ("corpse", "Any Corpse"),
]


@st.cache_data(ttl=3600)
def get_category_options() -> list[tuple[str, str]]:
    client = PoETradeClient()
    categories = [(cat.id, cat.label) for cat in client.get_trade_categories()]

    merged: list[tuple[str, str]] = []
    seen: set[str] = set()

    for cat_id, label in TYPE_FILTER_PRESETS:
        if cat_id in seen:
            continue
        merged.append((cat_id, label))
        seen.add(cat_id)

    source_categories = categories or FALLBACK_CATEGORY_OPTIONS
    for cat_id, label in source_categories:
        if cat_id in seen:
            continue
        merged.append((cat_id, label))
        seen.add(cat_id)

    return merged


def init_state() -> None:
    defaults = {
        "query_id": "",
        "result_ids": [],
        "offset": 0,
        "scanned": 0,
        "all_records": [],
        "latest_fetched_rows": [],
        "all_opportunities": [],
        "active_league": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_state() -> None:
    st.session_state.query_id = ""
    st.session_state.result_ids = []
    st.session_state.offset = 0
    st.session_state.scanned = 0
    st.session_state.all_records = []
    st.session_state.latest_fetched_rows = []
    st.session_state.all_opportunities = []
    st.session_state.active_league = ""


def get_trade_client() -> PoETradeClient:
    if "trade_client" not in st.session_state:
        st.session_state.trade_client = PoETradeClient()
    return st.session_state.trade_client


def normalize_league_name(league: str) -> str:
    normalized = " ".join((league or "").strip().split())
    if not normalized:
        return ""
    return LEAGUE_ALIASES.get(normalized.lower(), normalized)


def listing_url(league: str, query_id: str, listing_id: str) -> str:
    safe_league = quote(league, safe="")
    return (
        f"https://www.pathofexile.com/trade/search/{safe_league}/{query_id}"
        f"/live?iid={listing_id}"
    )


def listing_to_row(record: dict, divine_ratio: float, league: str, query_id: str) -> dict:
    item = record.get("item", {})
    listing = record.get("listing", {})
    listing_id = str(record.get("id", ""))
    item_name = (item.get("name") or "").strip()
    type_line = (item.get("typeLine") or "").strip()
    full_name = f"{item_name} {type_line}".strip() or "Unknown Item"

    price = listing.get("price")
    currency = ""
    amount = None
    chaos_equiv = None
    if isinstance(price, dict):
        currency = str(price.get("currency", ""))
        raw_amount = price.get("amount")
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            amount = None

        normalized = normalize_currency(currency)
        if amount is not None:
            if normalized == "chaos":
                chaos_equiv = round(amount, 2)
            elif normalized == "divine":
                chaos_equiv = round(amount * divine_ratio, 2)

    return {
        "Item": full_name,
        "Currency": currency or "Unknown",
        "Amount": amount,
        "Chaos equiv": chaos_equiv,
        "Indexed": listing.get("indexed", ""),
        "Link": listing_url(league, query_id, listing_id) if listing_id and query_id and league else "",
    }


def _get_normalized_price(record: dict) -> tuple[str, float | None]:
    listing = record.get("listing", {})
    price = listing.get("price")
    if not isinstance(price, dict):
        return "", None

    normalized = normalize_currency(str(price.get("currency", "")))
    raw_amount = price.get("amount")
    try:
        amount = float(raw_amount)
    except (TypeError, ValueError):
        return normalized, None
    return normalized, amount


def _passes_default_minimums(record: dict, divine_ratio: float) -> bool:
    currency, amount = _get_normalized_price(record)
    if amount is None:
        return False

    if currency == "divine":
        return amount >= 1.0
    if currency == "chaos":
        return amount >= divine_ratio
    return False


def scan_next_batch(
    client: PoETradeClient,
    divine_ratio: float,
    min_profit: float,
    max_scan: int,
) -> None:
    query_id = st.session_state.query_id
    result_ids = st.session_state.result_ids
    offset = st.session_state.offset

    if not query_id:
        st.error("Run a search first.")
        return

    if offset >= len(result_ids):
        st.error("No more results available for this search.")
        return

    if st.session_state.scanned >= max_scan:
        st.error(
            f"Safety limit reached ({max_scan} listings scanned). Increase 'Max listings to scan' to continue."
        )
        return

    remaining_safe = max_scan - st.session_state.scanned
    local_offset = offset
    requested_ids = 0
    records: list[dict] = []

    # Results are globally sorted by price and include non-chaos/divine entries.
    # Keep scanning forward until we collect up to PAGE_SIZE qualifying listings.
    while local_offset < len(result_ids) and requested_ids < remaining_safe and len(records) < PAGE_SIZE:
        chunk_size = min(PAGE_SIZE, remaining_safe - requested_ids)
        ids = result_ids[local_offset : local_offset + chunk_size]
        if not ids:
            break

        fetched = client.fetch_listings(query_id, ids)
        for record in fetched:
            if _passes_default_minimums(record, divine_ratio):
                records.append(record)
                if len(records) >= PAGE_SIZE:
                    break

        local_offset += len(ids)
        requested_ids += len(ids)

    if not records:
        st.warning(
            "No qualifying listings found in this scan range. "
            "Only chaos >= Divine->Chaos ratio and divine >= 1 are considered."
        )
    rows = [
        listing_to_row(record, divine_ratio, st.session_state.active_league, query_id)
        for record in records
    ]

    st.session_state.all_records.extend(records)
    opportunities = analyze_arbitrage(st.session_state.all_records, divine_ratio, min_profit)

    st.session_state.offset = local_offset
    st.session_state.scanned += requested_ids
    st.session_state.latest_fetched_rows = rows
    st.session_state.all_opportunities = opportunities


def render_results() -> None:
    fetched_rows = st.session_state.latest_fetched_rows
    st.write(f"### Latest fetched listings ({len(fetched_rows)})")
    if fetched_rows:
        st.dataframe(fetched_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No listings fetched yet. Start a search to load 10 listings.")

    st.write("### Arbitrage opportunities")
    opportunities = st.session_state.all_opportunities
    if not opportunities:
        st.info("No qualifying arbitrage opportunities found in scanned listings yet.")
        return

    table = []
    for row in opportunities:
        table.append(
            {
                "Item": str(row["item"]),
                "Listed Price (Divine)": float(row["buy_divine_amount"]),
                "Listed Price (Chaos)": float(row["sell_chaos_amount"]),
            }
        )

    st.dataframe(table, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="PoE1 Divine/Chaos Arbitrage", layout="wide")
    init_state()
    client = get_trade_client()

    st.title("Path of Exile 1 Arbitrage Scanner")
    st.caption(
        "Find listings where buying in divines and reselling in chaos may create a spread. "
        "This scans in small batches to reduce API pressure."
    )

    with st.sidebar:
        st.header("Search Settings")

        selected_league = st.selectbox("League", options=COMMON_LEAGUES, index=0)
        custom_league = st.text_input("Custom league (optional)", value="")
        league = custom_league.strip() or selected_league
        league = normalize_league_name(league)

        category_options = FALLBACK_CATEGORY_OPTIONS
        category_warning = ""
        try:
            category_options = get_category_options()
        except Exception:
            category_warning = "Using fallback categories. Could not load latest category list from trade API."

        category_map = {cid: label for cid, label in category_options}
        category_ids = [cid for cid, _ in category_options]
        selected_category_id = st.selectbox(
            "Item Cateogry",
            options=category_ids,
            index=0,
            format_func=lambda cid: category_map.get(cid, cid),
            help="Type Filters category from trade search.",
        )
        if category_warning:
            st.warning(category_warning)

        rarity_map = {rid: label for rid, label in RARITY_OPTIONS}
        rarity_ids = [rid for rid, _ in RARITY_OPTIONS]
        selected_rarity = st.selectbox(
            "Item Rarity",
            options=rarity_ids,
            index=0,
            format_func=lambda rid: rarity_map.get(rid, rid),
            help="Optional rarity filter from Type Filters.",
        )

        name_filter = st.text_input(
            "Filter by name (optional)",
            value="",
            help="Leave empty to search all items in the category. Enter text to filter results by item name.",
        )

        query_text = name_filter.strip()

        divine_ratio = st.number_input(
            "Divine -> Chaos ratio",
            min_value=1.0,
            value=80.0,
            step=1.0,
            help=(
                "Example: if 1 divine = 80 chaos, enter 80. "
                "Default listing filter uses divine >= 1 and chaos >= this value."
            ),
        )

        min_profit = st.number_input(
            "Minimum profit (chaos)",
            min_value=0.0,
            value=20.0,
            step=1.0,
            help="Filter out small spreads.",
        )

        max_scan = st.number_input(
            "Max listings to scan",
            min_value=10,
            value=DEFAULT_MAX_SCAN,
            step=10,
            help="Safety cap to avoid excessive requests. Each click scans up to 10 listings.",
        )

        start_search = st.button("Start new search", use_container_width=True)
        load_more = st.button(
            "Load 10 more listings",
            use_container_width=True,
            disabled=not bool(st.session_state.query_id),
        )

        telemetry = client.get_telemetry_snapshot()
        st.divider()
        st.caption("Request telemetry")
        st.caption(
            " | ".join(
                [
                    f"req={telemetry['request_count']}",
                    f"429={telemetry['rate_limit_count']}",
                    f"retry={telemetry['retry_count']}",
                    f"cache hit/miss={telemetry['cache_hits']}/{telemetry['cache_misses']}",
                ]
            )
        )
        st.caption(
            f"pace={telemetry['min_request_interval']}s, cache_ttl={telemetry['cache_ttl_seconds']}s"
        )

    if start_search:
        if not league:
            st.error("League is required.")
        else:
            try:
                reset_state()
                search = client.search_listings(
                    league,
                    query_text,
                    category_id=selected_category_id,
                    rarity_option=selected_rarity,
                    online_only=True,
                )

                st.session_state.query_id = search.query_id
                st.session_state.result_ids = search.result_ids
                st.session_state.active_league = league

                st.success(f"Search created. Total trade hits reported: {search.total}")
                if search.result_ids:
                    scan_next_batch(client, divine_ratio, min_profit, int(max_scan))
                else:
                    st.warning("Search succeeded but returned no listing IDs.")
            except RateLimitError as exc:
                st.error(f"Rate limit reached: {exc}")
            except TradeAPIError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

    if load_more:
        try:
            scan_next_batch(client, divine_ratio, min_profit, int(max_scan))
        except RateLimitError as exc:
            st.error(f"Rate limit reached: {exc}")
        except TradeAPIError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Unexpected error: {exc}")

    st.write("## Results")
    st.write(
        f"Scanned listings: {st.session_state.scanned} / {len(st.session_state.result_ids)}"
    )
    render_results()

    st.divider()
    st.markdown(
        "This tool is educational and depends on official trade API availability and current market behavior. "
        "Always verify item stats/rolls before buying."
    )


if __name__ == "__main__":
    main()
