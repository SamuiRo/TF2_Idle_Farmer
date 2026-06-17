"""
Steam Web Inventory API client.

Responsibilities:
- Fetch the TF2 inventory for a Steam ID via the public inventory endpoint
- Return item quantities suitable for before/after comparison
- Never raise exceptions; return None on failures

Endpoint:
    https://steamcommunity.com/inventory/{steamid}/440/2?l=english&count=5000
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from modules.logger import log


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TF2_APP_ID: str = "440"
_TF2_CONTEXT_ID: str = "2"
_INVENTORY_COUNT: int = 5000
_REQUEST_TIMEOUT_SEC: int = 15
_MAX_RETRIES: int = 3
_RETRY_DELAY_SEC: float = 5.0
_INVENTORY_URL: str = (
    "https://steamcommunity.com/inventory/{steamid}/{appid}/{contextid}"
    "?l=english&count={count}"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_inventory(steam_id: str, api_key: str) -> Counter[str] | None:
    """
    Fetch the TF2 inventory for *steam_id* and return item-name quantities.

    The public inventory endpoint currently does not require the API key, but
    the argument is kept so callers have one stable API.
    """
    if not steam_id or not steam_id.strip():
        log.warning("steam_inventory: empty steam_id provided - skipping.")
        return None

    steam_id = steam_id.strip()
    url = _INVENTORY_URL.format(
        steamid=steam_id,
        appid=_TF2_APP_ID,
        contextid=_TF2_CONTEXT_ID,
        count=_INVENTORY_COUNT,
    )

    log.debug(f"steam_inventory: fetching inventory for SteamID {steam_id}")

    raw = _fetch_json(url, steam_id)
    if raw is None:
        return None

    return _parse_item_names(raw, steam_id)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str, steam_id: str) -> dict | None:
    """
    Perform an HTTP GET to *url* with retries.
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
                log.warning(
                    f"steam_inventory: inventory is private for SteamID {steam_id} "
                    "(HTTP 403). Drop tracking via API unavailable for this account."
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
            return None

        except Exception as exc:  # noqa: BLE001
            log.warning(
                f"steam_inventory: unexpected error for SteamID {steam_id} "
                f"(attempt {attempt}/{_MAX_RETRIES}): {exc}"
            )

        if attempt < _MAX_RETRIES:
            log.debug(f"steam_inventory: retrying in {_RETRY_DELAY_SEC}s...")
            time.sleep(_RETRY_DELAY_SEC)

    log.error(
        f"steam_inventory: all {_MAX_RETRIES} attempts failed for SteamID {steam_id}. "
        "Falling back to console.log tracking."
    )
    return None


def _parse_item_names(raw: dict, steam_id: str) -> Counter[str] | None:
    """
    Extract item-name quantities from the raw inventory API response.
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
        log.debug(f"steam_inventory: inventory is empty for SteamID {steam_id}.")
        return Counter()

    class_to_name: dict[str, str] = {}
    for desc in descriptions:
        classid = str(desc.get("classid", ""))
        name = desc.get("market_name") or desc.get("name", "")
        if classid and name:
            class_to_name[classid] = name

    name_counts: Counter[str] = Counter()
    unknown_count = 0
    for asset in assets:
        classid = str(asset.get("classid", ""))
        name = class_to_name.get(classid)
        if name:
            name_counts[name] += 1
        else:
            unknown_count += 1

    if unknown_count:
        log.debug(
            f"steam_inventory: {unknown_count} asset(s) had no matching description "
            f"for SteamID {steam_id} - they will be ignored."
        )

    log.debug(
        f"steam_inventory: parsed {len(name_counts)} distinct item type(s) "
        f"({len(assets)} total assets) for SteamID {steam_id}."
    )
    return name_counts
