# PoE1 Divine/Chaos Arbitrage App

Small Python app that scans Path of Exile 1 trade listings and highlights potential spreads where:

- A listing priced in **divine** (converted to chaos using your manual ratio)
- Is cheaper than a comparable listing priced in **chaos**

Example: if 1 divine = 80 chaos and the same item appears at 1 divine and 140 chaos, the potential spread is 60 chaos.

## Features

- Manual divine-to-chaos ratio input
- League selection (with custom league override)
- Item Cateogry filter (Type Filters style, including Any / Any Weapon / One-handed weapon / Any Corpse)
- Item Rarity filter (Any / Normal / Magic / Rare / Unique)
- Scans in batches of 10 listings to reduce API pressure
- Always shows the latest fetched batch in a listings table (up to 10 rows)
- Shows arbitrage opportunities in a separate table
- "Load more" button to scan additional listings
- Safety cap (`Max listings to scan`) with clear error when reached
- Minimum profit filter (e.g. only show opportunities >= 20 chaos)

## Project Structure

- `app.py`: Streamlit UI and search flow
- `poe_trade.py`: PoE trade API client
- `arbitrage.py`: price parsing and spread logic

## Setup

1. Open a terminal in this folder.
2. Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Streamlit will print a local URL (usually `http://localhost:8501`). Open it in your browser.

## How To Use

1. Pick a league (or type a custom one, e.g. Allflame Standard).
2. Choose **Item Cateogry** (Type Filters) and optional **Item Rarity**.
3. Optionally enter **Filter by name** text.
4. Enter divine->chaos ratio (for example 80).
5. Set minimum profit filter (for example 20 chaos).
6. Click **Start new search**.
7. Click **Load 10 more listings** to continue scanning.

## Notes

- This app uses Path of Exile trade-site API endpoints (`/api/trade/*`).
- These trade endpoints are separate from the OAuth-protected endpoints listed in the developer API reference.
- Trade data can be stale or noisy. Always validate rolls/stats and liquidity before flipping.
- If rate-limited, wait and retry with smaller scans.

## Developer-Docs Alignment

- Category options are synced from trade metadata (`/api/trade/data/items`) with a local fallback list.
- Search requests use category IDs from API metadata.
- User-Agent follows the documented identifiable format and can be customized via env vars:
	- `POE_APP_ID` (default: `poe1-arbitrage-app`)
	- `POE_APP_VERSION` (default: `1.1.0`)
	- `POE_APP_CONTACT` (default: `local`)
- Rate-limit errors include `Retry-After` information when provided by the API.

## Third-Party Notice

This product isn't affiliated with or endorsed by Grinding Gear Games in any way.
