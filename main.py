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
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from typing import Any

import schedule

from modules import drop_tracker, human_behavior, steam_manager, tf2_manager
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


def load_accounts(settings: dict[str, Any]) -> list[str]:
    accounts_path = CONFIG_DIR / "accounts.txt"
    if not accounts_path.exists():
        log.error(f"accounts.txt not found: {accounts_path}")
        sys.exit(1)
    accounts = [
        line.strip()
        for line in accounts_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not accounts:
        log.error("accounts.txt is empty — nothing to do.")
        sys.exit(1)

    if settings.get("behavior", {}).get("shuffle_accounts", True):
        random.shuffle(accounts)
        log.info(f"Account order shuffled: {accounts}")
    else:
        log.info(f"Accounts loaded (fixed order): {accounts}")

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
# Core session logic
# ---------------------------------------------------------------------------

def run_account_session(account: str, servers: list[str], settings: dict[str, Any]) -> bool:
    """
    Execute a full idle farming session for *account*.

    Returns:
        True on success, False if the session failed and was skipped.
    """
    paths = settings["paths"]
    timing = settings["timing"]

    steam_exe: str = paths["steam_exe"]
    loginusers_vdf: str = paths["loginusers_vdf"]
    tf2_cfg_dir: str = paths["tf2_cfg_dir"]
    console_log_path: str = paths.get(
        "console_log",
        str(Path(tf2_cfg_dir).parent / "console.log"),
    )

    idle_min: float = timing["idle_duration_min"]
    idle_max: float = timing["idle_duration_max"]
    idle_duration: float = random.uniform(idle_min, idle_max)
    mouse_activity: bool = settings.get("behavior", {}).get("mouse_activity", True)

    log.info(f"{'=' * 50}")
    log.info(f"Starting session for account: {account}")
    log.info(f"{'=' * 50}")

    try:
        # ------------------------------------------------------------------
        # 1. Ensure Steam is stopped before touching loginusers.vdf
        # ------------------------------------------------------------------
        if steam_manager.is_steam_running():
            log.info("Steam is already running — shutting it down first.")
            steam_manager.quit_steam(steam_exe)
            human_behavior.wait(5, 10)

        # ------------------------------------------------------------------
        # 2. Switch account
        # ------------------------------------------------------------------
        steam_manager.switch_account(account, loginusers_vdf)

        # ------------------------------------------------------------------
        # 3. Launch Steam and wait for it to be ready
        # ------------------------------------------------------------------
        steam_manager.launch_steam(steam_exe, username=account)
        steam_ready = steam_manager.wait_for_steam_ready(
            timeout_sec=timing["steam_startup_wait"]
        )
        if not steam_ready:
            log.error(f"Steam did not start in time for account '{account}' — skipping.")
            return False

        # Additional human-like delay after Steam is visible (let it fully initialise)
        human_behavior.wait(
            timing.get("steam_warmup_min", 30),
            timing.get("steam_warmup_max", 60),
        )

        # ------------------------------------------------------------------
        # 4. Pick a server and generate autoexec.cfg
        # ------------------------------------------------------------------
        server = tf2_manager.get_random_server(servers)
        tf2_manager.generate_autoexec(server, tf2_cfg_dir)

        # Clear the TF2 console log so stale drops are not re-counted
        drop_tracker.clear_console_log(console_log_path)

        # ------------------------------------------------------------------
        # 5. Launch TF2
        # ------------------------------------------------------------------
        tf2_manager.launch_tf2(steam_exe)
        tf2_ready = tf2_manager.wait_for_tf2_ready(
            timeout_sec=timing["tf2_startup_wait"]
        )
        if not tf2_ready:
            log.error(f"TF2 did not start for '{account}' — skipping.")
            tf2_manager.quit_tf2()
            steam_manager.quit_steam(steam_exe)
            return False

        # Extra wait for TF2 to load map and connect via autoexec
        human_behavior.wait(20, 40)

        # Dismiss the server MOTD / welcome screen — without this the drop
        # timer does not start.  Death-drop popups do NOT need dismissal.
        human_behavior.dismiss_motd()

        log.info(f"TF2 connected to {server} — beginning idle session ({idle_duration:.1f} min)")

        # ------------------------------------------------------------------
        # 6. Idle session  (watcher runs in background the whole time)
        # ------------------------------------------------------------------
        watcher = drop_tracker.ConsoleLogWatcher(console_log_path, account)
        watcher.start()

        session_start = time.time()
        try:
            human_behavior.idle_session(idle_duration, mouse_activity=mouse_activity)
        finally:
            # Always stop the watcher — even if idle_session raises
            watcher.stop()

        actual_duration_min = (time.time() - session_start) / 60

        # ------------------------------------------------------------------
        # 7. Collect drops
        # ------------------------------------------------------------------
        # The watcher already logged drops live; check_and_save does a final
        # full-file scan and merges with live results before persisting.
        items = drop_tracker.check_and_save(
            account, console_log_path, actual_duration_min, watcher=watcher
        )
        if items:
            log.info(f"Items received for '{account}': {items}")
        else:
            log.info(f"No items this session for '{account}'.")

        # ------------------------------------------------------------------
        # 8. Clean up
        # ------------------------------------------------------------------
        tf2_manager.quit_tf2()
        human_behavior.wait(5, 10)
        steam_manager.quit_steam(steam_exe)

        log.info(f"Session completed for '{account}' — duration: {actual_duration_min:.1f} min")
        return True

    except Exception as exc:  # noqa: BLE001
        log.exception(f"Unexpected error during session for '{account}': {exc}")
        # Best-effort cleanup
        _emergency_cleanup()
        return False


def _emergency_cleanup() -> None:
    """Kill TF2 and Steam if a session crashes mid-way."""
    log.warning("Emergency cleanup: killing TF2 and Steam.")
    try:
        tf2_manager.quit_tf2()
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

    results: dict[str, bool] = {}

    for idx, account in enumerate(accounts, start=1):
        log.info(f"Processing account {idx}/{len(accounts)}: {account}")
        success = run_account_session(account, servers, settings)
        results[account] = success

        if idx < len(accounts):
            log.info("Pausing before next account…")
            human_behavior.wait(
                timing["pause_between_accounts_min"],
                timing["pause_between_accounts_max"],
            )

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
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
    log.info("Scheduler mode: weekly run every Monday at 09:00.")
    schedule.every().monday.at("09:00").do(run_weekly_farm)

    # Run immediately on first start so we don't wait a full week
    run_weekly_farm()

    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TF2 Idle Farmer")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run in scheduler mode (repeats every Monday at 09:00).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.schedule:
        run_with_scheduler()
    else:
        run_weekly_farm()