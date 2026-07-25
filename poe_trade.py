"""Minimal Path of Exile 1 trade API client."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time
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
        # Add light pacing to avoid bursty API traffic.
        self.min_request_interval = float(os.environ.get("POE_MIN_REQUEST_INTERVAL", "0.5"))
        self.max_rate_limit_retries = int(os.environ.get("POE_MAX_RATE_LIMIT_RETRIES", "2"))
        self.fetch_cache_ttl_seconds = float(os.environ.get("POE_FETCH_CACHE_TTL_SECONDS", "20"))
        self._last_request_at = 0.0
        self._fetch_cache: dict[tuple[str, tuple[str, ...]], tuple[float, list[dict[str, Any]]]] = {}
        self._request_count = 0
        self._rate_limit_count = 0
        self._retry_count = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._last_retry_after = 0.0
        self._last_rate_limit_state = ""
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
        for attempt in range(self.max_rate_limit_retries + 1):
            self._throttle_before_request()
            try:
                self._request_count += 1
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                raise TradeAPIError(f"Trade API request failed: {exc}") from exc
            finally:
                self._last_request_at = time.monotonic()

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
                self._rate_limit_count += 1
                self._last_rate_limit_state = rule_state or ""

                if attempt < self.max_rate_limit_retries:
                    delay = self._compute_retry_delay(retry_after, attempt)
                    self._retry_count += 1
                    self._last_retry_after = delay
                    time.sleep(delay)
                    continue

                raise RateLimitError(f"Trade API rate limit reached{extra}.")

            break

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

    def _throttle_before_request(self) -> None:
        if self.min_request_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)

    @staticmethod
    def _compute_retry_delay(retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                parsed = float(retry_after)
                if parsed > 0:
                    return parsed
            except ValueError:
                pass
        # Exponential backoff fallback when Retry-After is absent/invalid.
        return min(8.0, 1.0 * (2**attempt))

    def get_telemetry_snapshot(self) -> dict[str, Any]:
        return {
            "request_count": self._request_count,
            "rate_limit_count": self._rate_limit_count,
            "retry_count": self._retry_count,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "min_request_interval": self.min_request_interval,
            "last_retry_after": self._last_retry_after,
            "last_rate_limit_state": self._last_rate_limit_state,
            "cache_ttl_seconds": self.fetch_cache_ttl_seconds,
        }

    def _prune_cache(self) -> None:
        if not self._fetch_cache:
            return
        now = time.monotonic()
        expired_keys = [key for key, (expires_at, _) in self._fetch_cache.items() if now >= expires_at]
        for key in expired_keys:
            del self._fetch_cache[key]

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
        price_currency_option: str = "",
        sort_field: str = "price",
        sort_order: str = "asc",
    ) -> SearchResponse:
        url = f"{self.base_url}/api/trade/search/{league}"
        type_filters: dict[str, Any] = {}
        if category_id.strip():
            type_filters["category"] = {"option": category_id.strip()}
        if rarity_option.strip():
            type_filters["rarity"] = {"option": rarity_option.strip()}

        trade_filters: dict[str, Any] = {
            # Trade API option "priced" corresponds to listings with explicit buyout prices.
            "sale_type": {"option": "priced"}
        }
        if price_currency_option.strip():
            trade_filters["price"] = {"option": price_currency_option.strip()}

        query_body: dict[str, Any] = {
            "status": {"option": "online" if online_only else "any"},
        }
        query_filters: dict[str, Any] = {
            "trade_filters": {
                "filters": trade_filters,
            }
        }
        if type_filters:
            query_filters["type_filters"] = {
                "filters": type_filters,
            }
        query_body["filters"] = query_filters
        if query_text.strip():
            query_body["term"] = query_text.strip()

        payload: dict[str, Any] = {
            "query": query_body,
            "sort": {sort_field: sort_order},
        }

        data = self._request_json("POST", url, json=payload)
        query_id = str(data.get("id", ""))
        if not query_id:
            err = self._error_message_from_body(data)
            suffix = f" | API error {err}" if err else ""
            raise TradeAPIError(
                "Trade API search response is missing query id."
                f" league={league!r} category={category_id!r}{suffix}"
            )

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

        self._prune_cache()
        cache_key = (query_id, tuple(listing_ids))
        now = time.monotonic()
        cached = self._fetch_cache.get(cache_key)
        if cached and now < cached[0]:
            self._cache_hits += 1
            return cached[1]

        self._cache_misses += 1
        ids = ",".join(listing_ids)
        url = f"{self.base_url}/api/trade/fetch/{ids}?query={query_id}"

        data = self._request_json("GET", url)
        result = data.get("result", [])
        if not isinstance(result, list):
            return []

        normalized = [item for item in result if isinstance(item, dict)]
        expires_at = now + max(0.0, self.fetch_cache_ttl_seconds)
        self._fetch_cache[cache_key] = (expires_at, normalized)
        return normalized
