"""
Steam process and account management.

Responsibilities:
- Parse and modify loginusers.vdf to set the active account
- Launch / quit Steam
- Wait until Steam is fully initialised
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil
import vdf

from modules.constants import (
    STEAM_EXIT_POLL_INTERVAL_SEC,
    STEAM_EXIT_URI,
    STEAM_FLAG_LOGIN,
    STEAM_FLAG_NO_REACT_LOGIN,
    STEAM_FLAG_SILENT,
    STEAM_GRACEFUL_EXIT_TIMEOUT_SEC,
    STEAM_PROCESS_NAMES,
    STEAM_STABILISE_SEC,
    STEAM_START_POLL_INTERVAL_SEC,
)
from modules.logger import log


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_steam_exe(steam_exe_path: str) -> Path:
    path = Path(steam_exe_path)
    if not path.exists():
        raise FileNotFoundError(f"Steam executable not found: {path}")
    return path


def _find_account_key(users: dict, username: str) -> Optional[str]:
    """Return the SteamID64 key for *username* (case-insensitive), or None."""
    username_lower = username.lower()
    for steam_id, data in users.items():
        if data.get("AccountName", "").lower() == username_lower:
            return steam_id
    return None


# Known game / launcher process names that indicate someone is actively using
# the machine.  TF2 processes are included so a running idle session is also
# caught.  Extend this list if you run other Steam games.
_GAME_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        # TF2
        "hl2.exe",
        "tf_win64.exe",
        # Common Steam games / launchers that indicate active play
        "csgo.exe",
        "cs2.exe",
        "dota2.exe",
        "steamwebhelper.exe",   # intentionally NOT included — it's always up
    }
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_active_steam_account(loginusers_vdf_path: str) -> str | None:
    """
    Return the Steam account login name that is currently marked as MostRecent
    in loginusers.vdf, or None if the file cannot be read or no account is marked.

    This reflects the account Steam would log in as (or is already logged in as)
    — it does NOT make a live API call to Steam.

    Args:
        loginusers_vdf_path: Absolute path to loginusers.vdf.

    Returns:
        Lower-cased login name string, or None on any failure.
    """
    vdf_path = Path(loginusers_vdf_path)
    if not vdf_path.exists():
        log.debug(f"get_active_steam_account: VDF not found at {vdf_path}")
        return None
    try:
        with vdf_path.open("r", encoding="utf-8") as fh:
            data = vdf.load(fh)
        users: dict = data.get("users", {})
        for account_data in users.values():
            if account_data.get("MostRecent") == "1":
                name = account_data.get("AccountName", "")
                return name.lower() if name else None
    except Exception as exc:  # noqa: BLE001
        log.warning(f"get_active_steam_account: could not parse VDF: {exc}")
    return None


def is_game_running() -> bool:
    """
    Return True if any known game process (TF2, CS2, Dota 2, …) is currently
    running.

    Use this before starting a farming session to avoid kicking a user who is
    actively playing.  The check is intentionally conservative — only processes
    whose names are in ``_GAME_PROCESS_NAMES`` are considered.

    Returns:
        True if a game process is found, False otherwise.
    """
    running: list[str] = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info["name"].lower()
            if name in _GAME_PROCESS_NAMES:
                running.append(proc.info["name"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if running:
        log.debug(f"is_game_running: found game processes: {running}")
        return True
    return False


def switch_account(username: str, loginusers_vdf_path: str) -> None:
    """
    Edit loginusers.vdf so that *username* is marked as the most recent account.

    Steam must NOT be running when this is called; stop it first with
    :func:`quit_steam`.

    Args:
        username: Steam account login name (not persona/display name).
        loginusers_vdf_path: Absolute path to loginusers.vdf.

    Raises:
        FileNotFoundError: If the VDF file is missing.
        ValueError: If the account is not found in the file.
    """
    vdf_path = Path(loginusers_vdf_path)
    if not vdf_path.exists():
        raise FileNotFoundError(f"loginusers.vdf not found: {vdf_path}")

    log.info(f"Switching active account → '{username}'")

    with vdf_path.open("r", encoding="utf-8") as fh:
        data = vdf.load(fh)

    users: dict = data.get("users", {})
    if not users:
        raise ValueError("No users section found in loginusers.vdf")

    target_key = _find_account_key(users, username)
    if target_key is None:
        available = [v.get("AccountName") for v in users.values()]
        raise ValueError(
            f"Account '{username}' not found in loginusers.vdf. "
            f"Available accounts: {available}"
        )

    # Reset all accounts, then activate the target.
    # IMPORTANT: Do NOT touch RememberPassword of other accounts — setting it
    # to "0" makes Steam forget saved credentials and show the account-picker
    # GUI on next launch, which blocks automation.
    for steam_id, account_data in users.items():
        if steam_id == target_key:
            account_data["MostRecent"] = "1"
            account_data["WantsOfflineMode"] = "0"
            account_data["RememberPassword"] = "1"
        else:
            account_data["MostRecent"] = "0"
            # Leave RememberPassword and WantsOfflineMode untouched for
            # other accounts so their saved credentials stay intact.

    with vdf_path.open("w", encoding="utf-8") as fh:
        vdf.dump(data, fh, pretty=True)

    log.info(
        f"loginusers.vdf updated — MostRecent set to "
        f"'{username}' (SteamID: {target_key})"
    )


def launch_steam(
    steam_exe_path: str,
    username: Optional[str] = None,
    extra_args: Optional[list[str]] = None,
) -> None:
    """
    Start Steam as a detached background process.

    Args:
        steam_exe_path: Path to steam.exe.
        username: Steam login name.  When provided, ``-login <username>`` is
            passed so Steam skips the account-picker GUI entirely.
        extra_args: Optional additional command-line arguments.
    """
    exe = _find_steam_exe(steam_exe_path)

    base_args = [STEAM_FLAG_SILENT, STEAM_FLAG_NO_REACT_LOGIN]
    if username:
        base_args += [STEAM_FLAG_LOGIN, username]

    cmd = [str(exe)] + base_args + (extra_args or [])
    log.info(f"Launching Steam: {' '.join(cmd)}")
    subprocess.Popen(cmd, close_fds=True)


def quit_steam(steam_exe_path: Optional[str] = None) -> None:
    """
    Gracefully exit Steam via ``steam://exit``, then wait for the process to
    terminate.  Falls back to SIGTERM if Steam does not close within
    ``STEAM_GRACEFUL_EXIT_TIMEOUT_SEC``.

    Args:
        steam_exe_path: Path to steam.exe.  Required for a graceful shutdown;
            if omitted the process is force-killed after the deadline.
    """
    log.info(f"Requesting Steam shutdown via {STEAM_EXIT_URI}")
    if steam_exe_path:
        try:
            subprocess.Popen([steam_exe_path, STEAM_EXIT_URI], close_fds=True)
        except (FileNotFoundError, OSError) as exc:
            log.warning(
                f"Could not send {STEAM_EXIT_URI} via '{steam_exe_path}': "
                f"{exc} — will force-kill."
            )
    else:
        log.warning(
            "steam_exe_path not provided — "
            "skipping graceful shutdown, will force-kill."
        )

    deadline = time.time() + STEAM_GRACEFUL_EXIT_TIMEOUT_SEC
    while time.time() < deadline:
        if not is_steam_running():
            log.info("Steam exited cleanly.")
            return
        time.sleep(STEAM_EXIT_POLL_INTERVAL_SEC)

    log.warning("Steam did not exit gracefully — force-killing.")
    _kill_processes(STEAM_PROCESS_NAMES)


def is_steam_running() -> bool:
    """Return True if any Steam process is active."""
    return any(
        proc.name().lower() in STEAM_PROCESS_NAMES
        for proc in psutil.process_iter(["name"])
    )


def wait_for_steam_ready(
    timeout_sec: int = 60,
    stabilise_sec: int = STEAM_STABILISE_SEC,
) -> bool:
    """
    Block until Steam's main process is detected and has had time to log in,
    or until *timeout_sec* elapses.

    Steam's process appears almost immediately after launch, but the client
    needs additional time to authenticate and become ready to launch games.
    ``stabilise_sec`` is an extra fixed wait applied *after* the process is
    first detected to cover that login window.

    Args:
        timeout_sec:   Maximum time to wait for the process to appear.
        stabilise_sec: Extra seconds to wait after detection so Steam can
                       finish logging in.  Increase on slow machines / HDD.

    Returns:
        True if Steam became ready within the timeout, False otherwise.
    """
    log.info(f"Waiting for Steam to initialise (timeout={timeout_sec}s)…")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if is_steam_running():
            log.info(
                f"Steam process detected — "
                f"waiting {stabilise_sec}s for login to complete…"
            )
            time.sleep(stabilise_sec)
            log.info("Steam is ready.")
            return True
        time.sleep(STEAM_START_POLL_INTERVAL_SEC)

    log.error("Timed out waiting for Steam to start.")
    return False


# ---------------------------------------------------------------------------
# Internal utility
# ---------------------------------------------------------------------------

def _kill_processes(names: frozenset[str]) -> None:
    """Kill all processes whose lower-cased name is in *names*."""
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if proc.info["name"].lower() in names:
                log.debug(
                    f"Killing process {proc.info['name']} "
                    f"(PID {proc.info['pid']})"
                )
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass