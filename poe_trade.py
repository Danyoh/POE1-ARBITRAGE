"""Minimal Path of Exile 1 trade API client."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests


class TradeAPIError(Exception):
    """Raised when trade API communication fails."""


class RateLimitError(TradeAPIError):
    """Raised when the trade API returns a rate limit response."""


@dataclass
class SearchResponse:
    query_id: str
    result_ids: list[str]
    total: int


@dataclass(frozen=True)
class TradeCategory:
    id: str
    label: str


class PoETradeClient:
    """Small wrapper around the official PoE trade endpoints."""

    def __init__(self, base_url: str = "https://www.pathofexile.com", timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        app_id = os.environ.get("POE_APP_ID", "poe1-arbitrage-app")
        app_version = os.environ.get("POE_APP_VERSION", "1.1.0")
        contact = os.environ.get("POE_APP_CONTACT", "local")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": f"OAuth {app_id}/{app_version} (contact: {contact})",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    @staticmethod
    def _error_message_from_body(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        err = data.get("error")
        if not isinstance(err, dict):
            return ""
        code = err.get("code")
        message = err.get("message")
        if code is None and not message:
            return ""
        return f"{code}: {message}" if code is not None else str(message)

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise TradeAPIError(f"Trade API request failed: {exc}") from exc

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            rule_state = response.headers.get("X-Rate-Limit-Client-State") or response.headers.get(
                "X-Rate-Limit-IP-State"
            )
            details = []
            if retry_after:
                details.append(f"retry after {retry_after}s")
            if rule_state:
                details.append(f"state {rule_state}")
            extra = f" ({', '.join(details)})" if details else ""
            raise RateLimitError(f"Trade API rate limit reached{extra}.")

        parsed_error_msg = ""
        if not response.ok:
            try:
                parsed_error_msg = self._error_message_from_body(response.json())
            except ValueError:
                parsed_error_msg = ""

        if not response.ok:
            suffix = f" | API error {parsed_error_msg}" if parsed_error_msg else ""
            raise TradeAPIError(
                f"Trade API request failed with status {response.status_code}: {response.text[:300]}{suffix}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise TradeAPIError("Trade API returned non-JSON response.") from exc

        if not isinstance(data, dict):
            raise TradeAPIError("Trade API returned an unexpected response format.")

        return data

    def get_trade_categories(self) -> list[TradeCategory]:
        url = f"{self.base_url}/api/trade/data/items"
        data = self._request_json(
            "GET",
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.pathofexile.com/trade/search/Standard",
            },
        )

        raw_categories = data.get("result", [])
        if not isinstance(raw_categories, list):
            return []

        categories: list[TradeCategory] = []
        for item in raw_categories:
            if not isinstance(item, dict):
                continue
            cat_id = str(item.get("id", "")).strip()
            label = str(item.get("label", "")).strip()
            if cat_id and label:
                categories.append(TradeCategory(id=cat_id, label=label))

        categories.sort(key=lambda c: c.label.lower())
        return categories

    def search_listings(
        self,
        league: str,
        query_text: str,
        category_id: str,
        rarity_option: str = "",
        online_only: bool = True,
    ) -> SearchResponse:
        url = f"{self.base_url}/api/trade/search/{league}"
        type_filters: dict[str, Any] = {}
        if category_id.strip():
            type_filters["category"] = {"option": category_id.strip()}
        if rarity_option.strip():
            type_filters["rarity"] = {"option": rarity_option.strip()}

        query_body: dict[str, Any] = {
            "status": {"option": "online" if online_only else "any"},
        }
        if type_filters:
            query_body["filters"] = {
                "type_filters": {
                    "filters": type_filters,
                }
            }
        if query_text.strip():
            query_body["term"] = query_text.strip()

        payload: dict[str, Any] = {
            "query": query_body,
            "sort": {"price": "asc"},
        }

        data = self._request_json("POST", url, json=payload)
        query_id = str(data.get("id", ""))
        result_ids = data.get("result", [])
        if not isinstance(result_ids, list):
            result_ids = []

        return SearchResponse(
            query_id=query_id,
            result_ids=[str(x) for x in result_ids],
            total=int(data.get("total", 0)),
        )

    def fetch_listings(self, query_id: str, listing_ids: list[str]) -> list[dict[str, Any]]:
        if not listing_ids:
            return []
        ids = ",".join(listing_ids)
        url = f"{self.base_url}/api/trade/fetch/{ids}?query={query_id}"

        data = self._request_json("GET", url)
        result = data.get("result", [])
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]
