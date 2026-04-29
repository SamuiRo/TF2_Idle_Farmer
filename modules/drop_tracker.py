"""
TF2 drop tracking.

Responsibilities:
- Parse console.log for item-drop messages
- Persist drop history to data/drops.json
- Provide weekly summary statistics
- Clear console.log before each new session
"""

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from modules.logger import log


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex patterns that TF2 console.log uses when a weekly drop occurs.
# The game outputs something like:
#   TF_PLAYER_DROP_ITEM
#   You have found a The Holy Mackerel!
#
# NOTE: Only one pattern is needed — "You have found a …!" is always present
# when TF_PLAYER_DROP_ITEM fires, so matching it directly is sufficient and
# unambiguous.  The former second pattern captured the *entire* "You have
# found a …!" sentence as group 1 (wrong) and produced duplicate entries
# because it matched the same drop that pattern 1 already caught.
_DROP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"You have found a (.+?)!"),
]

DATA_DIR = Path(__file__).parent.parent / "data"
DROPS_FILE = DATA_DIR / "drops.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_console_log(console_log_path: str) -> list[str]:
    """
    Scan *console_log_path* for item-drop lines and return the item names.

    Args:
        console_log_path: Full path to TF2's console.log file.

    Returns:
        List of item names found (may be empty).
    """
    log_path = Path(console_log_path)
    if not log_path.exists():
        log.warning(f"console.log not found at: {log_path}")
        return []

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.error(f"Cannot read console.log: {exc}")
        return []

    found_items: list[str] = []
    for pattern in _DROP_PATTERNS:
        for match in pattern.finditer(content):
            item = match.group(1).strip()
            if item and item not in found_items:
                found_items.append(item)

    if found_items:
        log.info(f"Drops detected in console.log: {found_items}")
    else:
        log.info("No drops detected in console.log for this session.")

    return found_items


def save_drop(
    account: str,
    items: list[str],
    session_duration_min: float,
) -> None:
    """
    Append a drop record for *account* to drops.json.

    Args:
        account: Steam login name.
        items: List of item names obtained during the session.
        session_duration_min: How long the idle session ran (minutes).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = _load_drops()

    record: dict[str, Any] = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "items": items,
        "session_duration_min": round(session_duration_min, 1),
    }

    if account not in data:
        data[account] = []
    data[account].append(record)

    _save_drops(data)
    log.info(f"Drop record saved for '{account}': {items} ({session_duration_min:.1f} min)")


def check_and_save(
    account: str,
    console_log_path: str,
    session_duration_min: float,
) -> list[str]:
    """
    Convenience: parse the log and immediately persist the result.

    Args:
        account: Steam login name.
        console_log_path: Path to console.log.
        session_duration_min: Session length in minutes.

    Returns:
        List of item names found (may be empty).
    """
    items = parse_console_log(console_log_path)
    save_drop(account, items, session_duration_min)
    return items


def get_weekly_summary() -> dict[str, Any]:
    """
    Return per-account drop counts and item lists for the past 7 days.

    Returns:
        Dictionary keyed by account name with summary data.
    """
    data = _load_drops()
    cutoff = date.today() - timedelta(days=7)
    summary: dict[str, Any] = {}

    for account, records in data.items():
        weekly_items: list[str] = []
        session_count = 0

        for record in records:
            try:
                record_date = date.fromisoformat(record["date"])
            except (KeyError, ValueError):
                continue

            if record_date >= cutoff:
                session_count += 1
                weekly_items.extend(record.get("items", []))

        summary[account] = {
            "sessions": session_count,
            "total_drops": len(weekly_items),
            "items": weekly_items,
        }

    return summary


def clear_console_log(console_log_path: str) -> None:
    """
    Truncate console.log before a new session so old drops are not
    re-detected.

    Args:
        console_log_path: Full path to console.log.
    """
    log_path = Path(console_log_path)
    try:
        log_path.write_text("", encoding="utf-8")
        log.info(f"console.log cleared: {log_path}")
    except OSError as exc:
        log.warning(f"Could not clear console.log: {exc}")


def print_weekly_summary() -> None:
    """Log a human-readable weekly summary to the farmer log."""
    summary = get_weekly_summary()
    log.info("=== Weekly drop summary ===")
    for account, stats in summary.items():
        log.info(
            f"  {account}: {stats['sessions']} session(s), "
            f"{stats['total_drops']} drop(s) — {stats['items']}"
        )
    log.info("===========================")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_drops() -> dict[str, Any]:
    if DROPS_FILE.exists():
        try:
            return json.loads(DROPS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.error(f"drops.json is corrupted, starting fresh: {exc}")
    return {}


def _save_drops(data: dict[str, Any]) -> None:
    DROPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DROPS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )