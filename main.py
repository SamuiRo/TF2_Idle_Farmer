"""
TF2 Idle Farmer — main entry point.

Orchestrates the full weekly farming loop across multiple Steam accounts.
Run directly:
    python main.py

Or let the built-in scheduler run it every Monday at 09:00:
    python main.py --schedule
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

import schedule

from modules import (
    drop_tracker,
    human_behavior,
    notifier,
    steam_manager,
    tf2_health,
    tf2_manager,
)
from modules.constants import (
    CLEANUP_WAIT_MAX_SEC,
    CLEANUP_WAIT_MIN_SEC,
    INVENTORY_POST_SESSION_POLL_ATTEMPTS_DEFAULT,
    INVENTORY_POST_SESSION_POLL_INTERVAL_SEC_DEFAULT,
    SCHEDULE_DAY,
    SCHEDULE_POLL_SEC,
    SCHEDULE_TIME,
    SECONDS_PER_MINUTE,
    SESSION_MAP_LOAD_WAIT_MAX_SEC,
    SESSION_MAP_LOAD_WAIT_MIN_SEC,
    STEAM_WARMUP_MAX_SEC_DEFAULT,
    STEAM_WARMUP_MIN_SEC_DEFAULT,
)
from modules.logger import log


# ---------------------------------------------------------------------------
# Config loading helpers
# ---------------------------------------------------------------------------

CONFIG_DIR = Path(__file__).parent / "config"


def load_settings() -> dict[str, Any]:
    settings_path = CONFIG_DIR / "settings.toml"
    if not settings_path.exists():
        log.error(f"settings.toml not found: {settings_path}")
        sys.exit(1)
    with settings_path.open("rb") as fh:
        return tomllib.load(fh)


def load_accounts(settings: dict[str, Any]) -> list[dict[str, str | None]]:
    """
    Parse accounts.txt and return a list of account dicts.

    Supported formats (can be mixed in the same file):
        my_login                        → {"login": "my_login", "steam_id": None}
        my_login:76561198XXXXXXXXX      → {"login": "my_login", "steam_id": "76561198..."}

    Lines starting with '#' and blank lines are ignored.
    The Steam ID part (after ':') is optional — accounts without one fall
    back to console.log parsing for drop detection.
    """
    accounts_path = CONFIG_DIR / "accounts.txt"
    if not accounts_path.exists():
        log.error(f"accounts.txt not found: {accounts_path}")
        sys.exit(1)

    accounts: list[dict[str, str | None]] = []
    for raw_line in accounts_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            login, steam_id = line.split(":", 1)
            login = login.strip()
            steam_id = steam_id.strip() or None
        else:
            login = line
            steam_id = None

        if login:
            accounts.append({"login": login, "steam_id": steam_id})

    if not accounts:
        log.error("accounts.txt is empty — nothing to do.")
        sys.exit(1)

    if settings.get("behavior", {}).get("shuffle_accounts", True):
        random.shuffle(accounts)
        logins = [a["login"] for a in accounts]
        log.info(f"Account order shuffled: {logins}")
    else:
        logins = [a["login"] for a in accounts]
        log.info(f"Accounts loaded (fixed order): {logins}")

    return accounts


def load_servers(settings: dict[str, Any]) -> list[str]:
    servers_path = CONFIG_DIR / "servers.txt"
    if not servers_path.exists():
        log.error(f"servers.txt not found: {servers_path}")
        sys.exit(1)

    servers = [
        line.strip()
        for line in servers_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not servers:
        log.error("servers.txt is empty — no idle servers defined.")
        sys.exit(1)

    log.info(f"Servers loaded: {servers}")
    return servers


# ---------------------------------------------------------------------------
# Steam Inventory API helpers
# ---------------------------------------------------------------------------

def _get_api_key(settings: dict[str, Any]) -> str | None:
    """Return the Steam API key from settings, or None if not configured."""
    return settings.get("steam_api", {}).get("api_key") or None


def _try_inventory_snapshot(
    steam_id: str | None,
    api_key: str | None,
    label: str,
) -> Counter[str] | None:
    """
    Attempt to take an inventory snapshot.

    Returns item-name counts, or None if:
    - steam_id is absent
    - api_key is absent
    - the inventory is private / unreachable

    The *label* is used only for log messages (e.g. "before" / "after").
    """
    if not steam_id:
        log.debug(f"Inventory snapshot ({label}): no Steam ID — skipped.")
        return None
    if not api_key:
        log.debug(f"Inventory snapshot ({label}): no API key — skipped.")
        return None

    # Import here so the rest of the program works fine even if the module
    # has import errors (shouldn't happen, but defensive).
    try:
        from modules.steam_inventory import get_inventory  # noqa: PLC0415
    except ImportError as exc:
        log.warning(f"steam_inventory module unavailable: {exc}")
        return None

    log.info(
        f"Taking inventory snapshot ({label}) for SteamID {steam_id}…"
    )
    snapshot = get_inventory(steam_id, api_key)
    if snapshot is None:
        log.warning(
            f"Inventory snapshot ({label}) returned None for SteamID {steam_id}. "
            f"Will fall back to console.log for drop detection."
        )
    else:
        log.info(
            f"Inventory snapshot ({label}): {len(snapshot)} distinct item type(s)."
        )
    return snapshot


def _compute_new_items(
    before: Counter[str] | None,
    after: Counter[str] | None,
    account: str,
) -> list[str] | None:
    """
    Compute items that appeared after the session.

    Returns:
        A list of new item names if both snapshots are available,
        or None to signal "fall back to console.log".
    """
    if before is None or after is None:
        return None

    delta = after - before
    new_items = _format_inventory_delta(delta)
    if new_items:
        log.info(
            f"Inventory diff for '{account}': {len(new_items)} new item(s): "
            f"{new_items}"
        )
    else:
        log.info(f"Inventory diff for '{account}': no new items detected via API.")
    return new_items


def _try_post_session_inventory_snapshot(
    steam_id: str | None,
    api_key: str | None,
    before: Counter[str] | None,
    settings: dict[str, Any],
) -> Counter[str] | None:
    """
    Poll the inventory after TF2 exits so delayed Steam updates are not missed.
    """
    if before is None:
        return _try_inventory_snapshot(steam_id, api_key, "after")

    steam_api = settings.get("steam_api", {})
    attempts = _coerce_int(
        steam_api.get("inventory_poll_attempts"),
        INVENTORY_POST_SESSION_POLL_ATTEMPTS_DEFAULT,
        minimum=1,
    )
    interval_sec = _coerce_float(
        steam_api.get("inventory_poll_interval_sec"),
        INVENTORY_POST_SESSION_POLL_INTERVAL_SEC_DEFAULT,
        minimum=0.0,
    )

    latest: Counter[str] | None = None
    for attempt in range(1, attempts + 1):
        latest = _try_inventory_snapshot(
            steam_id,
            api_key,
            f"after {attempt}/{attempts}",
        )
        if latest is None:
            if attempt < attempts and interval_sec > 0:
                log.info(
                    "Post-session inventory snapshot failed; "
                    f"retrying in {interval_sec:.1f}s..."
                )
                time.sleep(interval_sec)
            continue

        delta = latest - before
        if delta:
            log.info(f"Inventory delta appeared on poll {attempt}/{attempts}.")
            return latest

        if attempt < attempts and interval_sec > 0:
            log.info(
                "No inventory delta yet; "
                f"waiting {interval_sec:.1f}s before poll {attempt + 1}/{attempts}."
            )
            time.sleep(interval_sec)

    return latest


def _format_inventory_delta(delta: Counter[str]) -> list[str]:
    """Format Counter deltas for drops.json/logging."""
    items: list[str] = []
    for name in sorted(delta):
        count = delta[name]
        if count <= 0:
            continue
        items.append(f"{name} (x{count})" if count > 1 else name)
    return items


def _coerce_int(value: Any, default: int, minimum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


def _coerce_float(value: Any, default: float, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


def _notify_connection_failure(
    settings: dict[str, Any],
    login: str,
    server: str,
    result: tf2_health.ConnectionCheckResult,
) -> None:
    """Send a best-effort alert for a failed TF2 server connection."""
    evidence = f"\nEvidence: {result.evidence}" if result.evidence else ""
    notifier.send_alert(
        settings,
        title="TF2 Idle Farmer: connection failed",
        message=(
            f"Account: {login}\n"
            f"Server: {server}\n"
            f"Reason: {result.reason}"
            f"{evidence}"
        ),
    )


# ---------------------------------------------------------------------------
# Core session logic
# ---------------------------------------------------------------------------

def run_account_session(
    account: dict[str, str | None],
    servers: list[str],
    settings: dict[str, Any],
) -> bool:
    """
    Execute a full idle farming session for *account*.

    Args:
        account:  Dict with keys "login" and "steam_id" (steam_id may be None).
        servers:  List of "IP:PORT" server strings.
        settings: Loaded settings.toml content.

    Returns:
        True on success, False if the session failed and was skipped.
    """
    login: str = account["login"]
    steam_id: str | None = account["steam_id"]

    paths = settings["paths"]
    timing = settings["timing"]
    behavior = settings.get("behavior", {})
    api_key = _get_api_key(settings)

    steam_exe: str = paths["steam_exe"]
    loginusers_vdf: str = paths["loginusers_vdf"]
    tf2_cfg_dir: str = paths["tf2_cfg_dir"]
    console_log_path: str = paths.get(
        "console_log",
        str(Path(tf2_cfg_dir).parent / "console.log"),
    )

    idle_duration: float = random.uniform(
        timing["idle_duration_min"],
        timing["idle_duration_max"],
    )
    mouse_activity: bool = behavior.get("mouse_activity", True)

    log.info("=" * 50)
    log.info(f"Starting session for account: {login}")
    if steam_id:
        log.info(f"  Steam ID: {steam_id}")
    log.info("=" * 50)

    try:
        # ------------------------------------------------------------------
        # 0. Safety guard — abort if someone is actively playing right now
        # ------------------------------------------------------------------
        if steam_manager.is_game_running():
            log.warning(
                "A game process is currently running — "
                "aborting session for '%s' to avoid interrupting active play. "
                "Close the game and restart the farmer when ready.",
                login,
            )
            return False

        # ------------------------------------------------------------------
        # 1. Check whether the correct Steam account is already active
        # ------------------------------------------------------------------
        steam_is_running = steam_manager.is_steam_running()
        active_account = (
            steam_manager.get_active_steam_account(loginusers_vdf)
            if steam_is_running
            else None
        )
        already_logged_in = (
            steam_is_running
            and active_account is not None
            and active_account == login.lower()
        )

        if already_logged_in:
            log.info(
                f"Steam is already running as '{login}' — "
                "skipping shutdown / re-login."
            )
        else:
            # ---------------------------------------------------------------
            # 1b. Wrong (or no) account is active — need to switch
            # ---------------------------------------------------------------
            if steam_is_running:
                if active_account:
                    log.info(
                        f"Steam is running as '{active_account}', "
                        f"need '{login}' — restarting Steam."
                    )
                else:
                    log.info(
                        "Steam is running but active account is unknown — "
                        "restarting Steam to be safe."
                    )
                steam_manager.quit_steam(steam_exe)
                human_behavior.wait(CLEANUP_WAIT_MIN_SEC, CLEANUP_WAIT_MAX_SEC)

            # ---------------------------------------------------------------
            # 2. Switch account in loginusers.vdf
            # ---------------------------------------------------------------
            steam_manager.switch_account(login, loginusers_vdf)

            # ---------------------------------------------------------------
            # 3. Launch Steam and wait for it to be ready
            # ---------------------------------------------------------------
            steam_manager.launch_steam(steam_exe, username=login)
            steam_ready = steam_manager.wait_for_steam_ready(
                timeout_sec=timing["steam_startup_wait"]
            )
            if not steam_ready:
                log.error(
                    f"Steam did not start in time for account '{login}' — skipping."
                )
                return False

            # Additional human-like delay after Steam is visible
            human_behavior.wait(
                timing.get("steam_warmup_min", STEAM_WARMUP_MIN_SEC_DEFAULT),
                timing.get("steam_warmup_max", STEAM_WARMUP_MAX_SEC_DEFAULT),
            )

        # ------------------------------------------------------------------
        # 4. Pick a server and generate autoexec.cfg
        # ------------------------------------------------------------------
        server = tf2_manager.get_random_server(servers)
        tf2_manager.generate_autoexec(server, tf2_cfg_dir)

        # Clear the TF2 console log so stale drops are not re-counted
        drop_tracker.clear_console_log(console_log_path)

        # ------------------------------------------------------------------
        # 4b. Take pre-session inventory snapshot (if API configured)
        # ------------------------------------------------------------------
        snapshot_before = _try_inventory_snapshot(steam_id, api_key, "before")

        # ------------------------------------------------------------------
        # 5. Launch TF2
        # ------------------------------------------------------------------
        tf2_manager.launch_tf2(steam_exe)
        tf2_ready = tf2_manager.wait_for_tf2_ready(
            timeout_sec=timing["tf2_startup_wait"]
        )
        if not tf2_ready:
            log.error(f"TF2 did not start for '{login}' — skipping.")
            tf2_manager.quit_tf2()
            tf2_manager.cleanup_autoexec(tf2_cfg_dir)
            steam_manager.quit_steam(steam_exe)
            return False

        # Wait for map load + autoexec connect
        human_behavior.wait(
            SESSION_MAP_LOAD_WAIT_MIN_SEC, SESSION_MAP_LOAD_WAIT_MAX_SEC
        )

        # Verify that TF2 actually reached the selected server before idling.
        connection_result = tf2_health.check_tf2_connection(
            console_log_path,
            settings.get("connection_check", {}),
        )
        if not connection_result.ok:
            log.error(
                f"TF2 connection check failed for '{login}' on {server}: "
                f"{connection_result.reason}"
            )
            if connection_result.evidence:
                log.error(f"Connection check evidence: {connection_result.evidence}")
            _notify_connection_failure(settings, login, server, connection_result)
            tf2_manager.quit_tf2()
            tf2_manager.cleanup_autoexec(tf2_cfg_dir)
            steam_manager.quit_steam(steam_exe)
            return False

        log.info(f"TF2 connection check passed: {connection_result.reason}")

        # Dismiss the server MOTD / welcome screen - without this the drop
        # timer does not start. Death-drop popups do NOT need dismissal.
        human_behavior.dismiss_motd()

        log.info(
            f"TF2 connected to {server} — "
            f"beginning idle session ({idle_duration:.1f} min)"
        )

        # ------------------------------------------------------------------
        # 6. Idle session (watcher runs in background the whole time)
        # ------------------------------------------------------------------
        watcher = drop_tracker.ConsoleLogWatcher(console_log_path, login)
        watcher.start()

        session_start = time.time()
        try:
            human_behavior.idle_session(
                idle_duration,
                mouse_activity=mouse_activity,
                drop_popup_config=behavior,
            )
        finally:
            # Always stop the watcher — even if idle_session raises
            watcher.stop()

        actual_duration_min = (time.time() - session_start) / SECONDS_PER_MINUTE

        # ------------------------------------------------------------------
        # 7. Quit TF2 before taking the post-session snapshot
        #    (so the drop is already committed to the Steam inventory)
        # ------------------------------------------------------------------
        tf2_manager.quit_tf2()
        tf2_manager.cleanup_autoexec(tf2_cfg_dir)
        human_behavior.wait(CLEANUP_WAIT_MIN_SEC, CLEANUP_WAIT_MAX_SEC)

        # ------------------------------------------------------------------
        # 7b. Take post-session inventory snapshot and compute diff
        # ------------------------------------------------------------------
        snapshot_after = _try_post_session_inventory_snapshot(
            steam_id,
            api_key,
            snapshot_before,
            settings,
        )
        api_items = _compute_new_items(snapshot_before, snapshot_after, login)

        # ------------------------------------------------------------------
        # 8. Collect drops
        # ------------------------------------------------------------------
        if api_items is not None:
            # API diff is authoritative — merge with anything the watcher
            # caught live (belt-and-suspenders: watcher names may differ
            # slightly from market_name, so we keep both)
            items = api_items
            for watcher_item in watcher.found_items:
                if watcher_item not in items:
                    log.info(
                        f"  + console.log item not in API diff (keeping): "
                        f"'{watcher_item}'"
                    )
                    items.append(watcher_item)
            drop_tracker.save_drop(login, items, actual_duration_min)
        else:
            # Fallback: use console.log (existing logic unchanged)
            items = drop_tracker.check_and_save(
                login, console_log_path, actual_duration_min, watcher=watcher
            )

        if items:
            log.info(f"Items received for '{login}': {items}")
        else:
            log.info(f"No items this session for '{login}'.")

        # ------------------------------------------------------------------
        # 9. Quit Steam
        # ------------------------------------------------------------------
        steam_manager.quit_steam(steam_exe)

        log.info(
            f"Session completed for '{login}' — "
            f"duration: {actual_duration_min:.1f} min"
        )
        return True

    except Exception as exc:  # noqa: BLE001
        log.exception(f"Unexpected error during session for '{login}': {exc}")
        _emergency_cleanup(tf2_cfg_dir)
        return False


def _emergency_cleanup(tf2_cfg_dir: str | None = None) -> None:
    """Kill TF2 and Steam if a session crashes mid-way, and remove autoexec."""
    log.warning("Emergency cleanup: killing TF2 and Steam.")
    try:
        tf2_manager.quit_tf2()
    except Exception:  # noqa: BLE001
        pass
    if tf2_cfg_dir:
        try:
            tf2_manager.cleanup_autoexec(tf2_cfg_dir)
        except Exception:  # noqa: BLE001
            pass
    try:
        steam_manager.quit_steam()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Full weekly run
# ---------------------------------------------------------------------------

def run_weekly_farm() -> None:
    """Run the full farming cycle for all configured accounts."""
    log.info("╔══════════════════════════════════════╗")
    log.info("║     TF2 Idle Farmer — Weekly Run     ║")
    log.info("╚══════════════════════════════════════╝")

    settings = load_settings()
    accounts = load_accounts(settings)
    servers = load_servers(settings)
    timing = settings["timing"]

    # Log whether API mode is active
    api_key = _get_api_key(settings)
    if api_key:
        accounts_with_id = sum(1 for a in accounts if a["steam_id"])
        log.info(
            f"Steam Inventory API: enabled "
            f"({accounts_with_id}/{len(accounts)} accounts have a Steam ID)"
        )
    else:
        log.info(
            "Steam Inventory API: disabled (no api_key in [steam_api] section). "
            "Drop detection via console.log only."
        )

    # If Steam is already running as one of the configured accounts, move that
    # account to the front of the queue so we start farming it immediately
    # without an unnecessary Steam restart.
    if steam_manager.is_steam_running():
        active_login = steam_manager.get_active_steam_account(
            settings["paths"]["loginusers_vdf"]
        )
        if active_login:
            matching = [a for a in accounts if a["login"].lower() == active_login]
            if matching:
                accounts = matching + [a for a in accounts if a not in matching]
                log.info(
                    f"Steam is already running as '{active_login}' — "
                    f"moving it to the front of the queue."
                )

    results: dict[str, bool] = {}

    for idx, account in enumerate(accounts, start=1):
        login = account["login"]
        log.info(f"Processing account {idx}/{len(accounts)}: {login}")
        success = run_account_session(account, servers, settings)
        results[login] = success

        if idx < len(accounts):
            log.info("Pausing before next account…")
            human_behavior.wait(
                timing["pause_between_accounts_min"],
                timing["pause_between_accounts_max"],
            )

    # Final summary
    log.info("=== Session results ===")
    for acc, ok in results.items():
        status = "✓ success" if ok else "✗ skipped"
        log.info(f"  {acc}: {status}")

    drop_tracker.print_weekly_summary()
    log.info("Weekly farming run complete.")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def run_with_scheduler() -> None:
    """Register the weekly job and block forever."""
    log.info(
        f"Scheduler mode: weekly run every {SCHEDULE_DAY} at {SCHEDULE_TIME}."
    )
    getattr(schedule.every(), SCHEDULE_DAY).at(SCHEDULE_TIME).do(run_weekly_farm)

    # Run immediately on first start so we don't wait a full week
    run_weekly_farm()

    while True:
        schedule.run_pending()
        time.sleep(SCHEDULE_POLL_SEC)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TF2 Idle Farmer")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help=(
            f"Run in scheduler mode "
            f"(repeats every {SCHEDULE_DAY} at {SCHEDULE_TIME})."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.schedule:
        run_with_scheduler()
    else:
        run_weekly_farm()
