"""
Steam Web Inventory API client.

Responsibilities:
- Fetch a TF2 inventory for a given Steam ID via the public Steam API
- Return a set of item names suitable for before/after comparison
- Never raise exceptions — always safe to call, returns None on any failure

API endpoint used:
    https://steamcommunity.com/inventory/{steamid}/440/2?l=english&count=5000

Requires:
    - A free Steam Web API key from https://steamcommunity.com/dev/apikey
    - The target account's inventory must be set to Public
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import json

from modules.logger import log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# TF2 App ID and context ID for the inventory endpoint
_TF2_APP_ID: str = "440"
_TF2_CONTEXT_ID: str = "2"

# Maximum items to fetch per request (Steam's hard cap)
_INVENTORY_COUNT: int = 5000

# Request timeout (seconds)
_REQUEST_TIMEOUT_SEC: int = 15

# Retry settings
_MAX_RETRIES: int = 3
_RETRY_DELAY_SEC: float = 5.0

# Base URL for the inventory endpoint
_INVENTORY_URL: str = (
    "https://steamcommunity.com/inventory/{steamid}/{appid}/{contextid}"
    "?l=english&count={count}"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_inventory(steam_id: str, api_key: str) -> set[str] | None:
    """
    Fetch the TF2 inventory for *steam_id* and return a set of item names.

    This function is intentionally fault-tolerant:
    - Returns None if the inventory is private or the account doesn't exist
    - Returns None on any network / API error
    - Never raises an exception

    Args:
        steam_id: 64-bit Steam ID as a string (e.g. "76561198XXXXXXXXX").
        api_key:  Steam Web API key (currently unused by the inventory
                  endpoint but kept for future authenticated endpoints).

    Returns:
        A set of item name strings, or None on any failure.
    """
    if not steam_id or not steam_id.strip():
        log.warning("steam_inventory: empty steam_id provided — skipping.")
        return None

    steam_id = steam_id.strip()
    url = _INVENTORY_URL.format(
        steamid=steam_id,
        appid=_TF2_APP_ID,
        contextid=_TF2_CONTEXT_ID,
        count=_INVENTORY_COUNT,
    )

    log.debug(f"steam_inventory: fetching inventory for SteamID {steam_id}")

    raw: dict | None = _fetch_json(url, steam_id)
    if raw is None:
        return None

    return _parse_item_names(raw, steam_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str, steam_id: str) -> dict | None:
    """
    Perform an HTTP GET to *url* with retries.

    Returns the parsed JSON dict on success, None on any failure.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "TF2IdleFarmer/1.0"},
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SEC) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8", errors="replace")
                    return json.loads(body)

                log.warning(
                    f"steam_inventory: HTTP {resp.status} for SteamID {steam_id} "
                    f"(attempt {attempt}/{_MAX_RETRIES})"
                )

        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                # 403 = inventory is private — no point retrying
                log.warning(
                    f"steam_inventory: inventory is private for SteamID {steam_id} "
                    f"(HTTP 403). Drop tracking via API unavailable for this account."
                )
                return None
            log.warning(
                f"steam_inventory: HTTP error {exc.code} for SteamID {steam_id} "
                f"(attempt {attempt}/{_MAX_RETRIES}): {exc.reason}"
            )

        except urllib.error.URLError as exc:
            log.warning(
                f"steam_inventory: network error for SteamID {steam_id} "
                f"(attempt {attempt}/{_MAX_RETRIES}): {exc.reason}"
            )

        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning(
                f"steam_inventory: failed to parse response for SteamID {steam_id}: {exc}"
            )
            return None  # Malformed JSON won't improve on retry

        except Exception as exc:  # noqa: BLE001
            log.warning(
                f"steam_inventory: unexpected error for SteamID {steam_id} "
                f"(attempt {attempt}/{_MAX_RETRIES}): {exc}"
            )

        if attempt < _MAX_RETRIES:
            log.debug(f"steam_inventory: retrying in {_RETRY_DELAY_SEC}s…")
            time.sleep(_RETRY_DELAY_SEC)

    log.error(
        f"steam_inventory: all {_MAX_RETRIES} attempts failed for SteamID {steam_id}. "
        f"Falling back to console.log tracking."
    )
    return None


def _parse_item_names(raw: dict, steam_id: str) -> set[str] | None:
    """
    Extract item names from the raw Steam inventory API response.

    The API returns:
    {
        "assets": [{"assetid": "...", "classid": "...", ...}, ...],
        "descriptions": [{"classid": "...", "market_name": "...", ...}, ...],
        "success": 1
    }

    We join assets → descriptions on classid and collect market_name values.
    A single item type can appear multiple times in assets (different assetids)
    but once in descriptions.  We build a multiset (Counter) of names so that
    before/after subtraction correctly handles quantities > 1.

    Args:
        raw:      Parsed JSON from the Steam inventory endpoint.
        steam_id: Used only for log messages.

    Returns:
        A set of "name (xN)" strings (with count suffix when N > 1),
        or None if the response structure is unexpected.
    """
    if not raw.get("success"):
        log.warning(
            f"steam_inventory: API reported failure for SteamID {steam_id} "
            f"(success={raw.get('success')!r}). Inventory may be private."
        )
        return None

    descriptions: list[dict] = raw.get("descriptions", [])
    assets: list[dict] = raw.get("assets", [])

    if not descriptions and not assets:
        # Empty inventory is a valid state — return empty set, not None
        log.debug(f"steam_inventory: inventory is empty for SteamID {steam_id}.")
        return set()

    # Build classid → market_name lookup from descriptions
    class_to_name: dict[str, str] = {}
    for desc in descriptions:
        classid = str(desc.get("classid", ""))
        name = desc.get("market_name") or desc.get("name", "")
        if classid and name:
            class_to_name[classid] = name

    # Count occurrences of each item name across all assets
    name_counts: dict[str, int] = {}
    unknown_count = 0
    for asset in assets:
        classid = str(asset.get("classid", ""))
        name = class_to_name.get(classid)
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
        else:
            unknown_count += 1

    if unknown_count:
        log.debug(
            f"steam_inventory: {unknown_count} asset(s) had no matching description "
            f"for SteamID {steam_id} — they will be ignored."
        )

    # Encode quantity into the key so set subtraction works correctly.
    # E.g. if before has {"Hat (x3)"} and after has {"Hat (x5)"},
    # the difference will include {"Hat (x5)"} and the caller can see a change.
    # Simple but effective for the "spot new items" use-case.
    item_set: set[str] = set()
    for name, count in name_counts.items():
        key = f"{name} (x{count})" if count > 1 else name
        item_set.add(key)

    log.debug(
        f"steam_inventory: parsed {len(item_set)} distinct item type(s) "
        f"({len(assets)} total assets) for SteamID {steam_id}."
    )
    return item_set