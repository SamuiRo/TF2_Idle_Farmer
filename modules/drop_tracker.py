"""
TF2 drop tracking.

Responsibilities:
- Parse console.log for item-drop messages
- Persist drop history to data/drops.json
- Provide weekly summary statistics
- Clear console.log before each new session
- Live-tail console.log during a session via ConsoleLogWatcher
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from modules.constants import (
    CONSOLE_LOG_POLL_INTERVAL_SEC,
    DROP_LOG_PATTERNS,
    DROPS_TIMESTAMP_TIMESPEC,
    WEEKLY_SUMMARY_DAYS,
)
from modules.logger import log

if TYPE_CHECKING:
    pass  # kept for future type-only imports


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent.parent / "data"
DROPS_FILE = DATA_DIR / "drops.json"

# Compiled regex patterns (compiled once at import time)
_COMPILED_DROP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in DROP_LOG_PATTERNS
]


# ---------------------------------------------------------------------------
# Live watcher
# ---------------------------------------------------------------------------

class ConsoleLogWatcher:
    """
    Background thread that tails console.log during an idle session and
    logs each item drop the moment TF2 writes it to disk.

    Usage::

        watcher = ConsoleLogWatcher(console_log_path, account)
        watcher.start()
        # ... idle session runs ...
        watcher.stop()
        live_items = watcher.found_items   # items seen during the session

    The watcher polls the file every ``poll_interval`` seconds, tracking the
    read position so it only processes *new* lines — not the whole file each
    time.  When the session ends, :meth:`stop` does one final scan to make
    sure nothing written in the last poll window is missed.

    It is safe to call :meth:`stop` multiple times.
    """

    def __init__(
        self,
        console_log_path: str,
        account: str,
        poll_interval: float = CONSOLE_LOG_POLL_INTERVAL_SEC,
    ) -> None:
        self._path = Path(console_log_path)
        self._account = account
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"ConsoleLogWatcher-{account}",
            daemon=True,
        )
        self._read_pos = 0
        self._lock = threading.Lock()
        self.found_items: list[str] = []

    def start(self) -> None:
        """Start the background watcher thread."""
        log.info(
            f"ConsoleLogWatcher started for '{self._account}' "
            f"(polling every {self._poll_interval}s)"
        )
        self._thread.start()

    def stop(self) -> None:
        """
        Signal the watcher to stop, do a final scan, then wait for the
        thread to finish.
        """
        self._stop_event.set()
        self._thread.join(timeout=self._poll_interval + 5)
        # Final scan: catch anything written between the last poll and now
        self._poll()
        log.info(f"ConsoleLogWatcher stopped for '{self._account}'")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll()
            self._stop_event.wait(timeout=self._poll_interval)

    def _poll(self) -> None:
        """Read any new bytes from console.log and scan for drops."""
        try:
            if not self._path.exists():
                return
            with self._path.open("r", encoding="utf-8", errors="replace") as fh:
                with self._lock:
                    fh.seek(self._read_pos)
                    new_text = fh.read()
                    self._read_pos = fh.tell()
            if new_text:
                self._scan(new_text)
        except OSError as exc:
            log.debug(f"ConsoleLogWatcher read error (non-critical): {exc}")

    def _scan(self, text: str) -> None:
        """Check new text for drop messages and log each one immediately."""
        for pattern in _COMPILED_DROP_PATTERNS:
            for match in pattern.finditer(text):
                item = match.group(1).strip()
                if not item:
                    continue
                with self._lock:
                    if item in self.found_items:
                        continue
                    self.found_items.append(item)
                log.info(
                    f"🎁 DROP DETECTED (live): '{item}' — "
                    f"account: '{self._account}'"
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_console_log(console_log_path: str) -> list[str]:
    """
    Scan *console_log_path* for item-drop lines and return the item names.

    Used at the end of a session as a final authoritative read.  If a
    :class:`ConsoleLogWatcher` was active during the session, pass its
    ``found_items`` list directly to :func:`save_drop` instead of calling this.

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
    for pattern in _COMPILED_DROP_PATTERNS:
        for match in pattern.finditer(content):
            item = match.group(1).strip()
            if item and item not in found_items:
                found_items.append(item)

    if found_items:
        log.info(f"Final scan — drops confirmed: {found_items}")
    else:
        log.info("Final scan — no drops found in console.log.")

    return found_items


def save_drop(
    account: str,
    items: list[str],
    session_duration_min: float,
) -> None:
    """
    Append a drop record for *account* to drops.json.

    Args:
        account:              Steam login name.
        items:                List of item names obtained during the session.
        session_duration_min: How long the idle session ran (minutes).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = _load_drops()

    record: dict[str, Any] = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(timespec=DROPS_TIMESTAMP_TIMESPEC),
        "items": items,
        "session_duration_min": round(session_duration_min, 1),
    }

    if account not in data:
        data[account] = []
    data[account].append(record)

    _save_drops(data)
    log.info(
        f"Drop record saved for '{account}': "
        f"{items} ({session_duration_min:.1f} min)"
    )


def check_and_save(
    account: str,
    console_log_path: str,
    session_duration_min: float,
    watcher: "ConsoleLogWatcher | None" = None,
) -> list[str]:
    """
    Collect drops and persist the result.

    If a *watcher* is supplied its already-collected ``found_items`` are used
    directly (the live items are authoritative); a final full-file scan is
    still run to catch anything the watcher may have missed in the last poll
    window, and the two lists are merged.

    Args:
        account:              Steam login name.
        console_log_path:     Path to console.log.
        session_duration_min: Session length in minutes.
        watcher:              Optional :class:`ConsoleLogWatcher` that ran
                              during the session.

    Returns:
        List of item names found (may be empty).
    """
    # Final authoritative scan (catches last-second drops after watcher stops)
    final_items = parse_console_log(console_log_path)

    if watcher is not None:
        # Merge: start from live items, add anything the final scan found
        # that the watcher didn't catch (should be rare).
        items = list(watcher.found_items)
        for item in final_items:
            if item not in items:
                log.info(
                    f"🎁 DROP found in final scan (missed by watcher): '{item}'"
                )
                items.append(item)
    else:
        items = final_items

    save_drop(account, items, session_duration_min)
    return items


def get_weekly_summary() -> dict[str, Any]:
    """
    Return per-account drop counts and item lists for the past
    ``WEEKLY_SUMMARY_DAYS`` days.

    Returns:
        Dictionary keyed by account name with summary data.
    """
    data = _load_drops()
    cutoff = date.today() - timedelta(days=WEEKLY_SUMMARY_DAYS)
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
    Delete console.log before a new session so old drops are not re-detected.

    The file is *deleted* rather than truncated.  TF2 opens console.log in
    append mode and caches the file descriptor; if the file is merely
    truncated to zero bytes, TF2 keeps writing at the old end-of-file
    offset, leaving a block of null bytes at the start of the file and
    causing all subsequent log lines to be unreadable until TF2 restarts.
    Deleting the file forces TF2 to create a fresh one the next time it
    writes a log line, so the file always starts at offset 0.

    Args:
        console_log_path: Full path to console.log.
    """
    log_path = Path(console_log_path)
    if not log_path.exists():
        log.info(
            f"console.log does not exist yet — nothing to clear: {log_path}"
        )
        return
    try:
        log_path.unlink()
        log.info(
            f"console.log deleted (will be recreated by TF2): {log_path}"
        )
    except OSError as exc:
        log.warning(f"Could not delete console.log: {exc}")


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