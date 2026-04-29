"""
Steam process and account management.

Responsibilities:
- Parse and modify loginusers.vdf to set the active account
- Launch / quit Steam
- Wait until Steam is fully initialised
"""

import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil
import vdf

from modules.logger import log


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STEAM_PROCESS_NAMES = {"steam.exe", "steamwebhelper.exe"}


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def switch_account(username: str, loginusers_vdf_path: str) -> None:
    """
    Edit loginusers.vdf so that *username* is marked as the most recent account.

    Steam must NOT be running when this is called; stop it first with
    `quit_steam()`.

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

    log.info(f"loginusers.vdf updated — MostRecent set to '{username}' (SteamID: {target_key})")


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
    # -silent        — start minimised to tray, no splash screen
    # -noreactlogin  — disable the newer React-based login UI (older flow
    #                  is more automation-friendly on some Steam versions)
    # -login <user>  — tell Steam which account to sign in to automatically
    base_args = ["-silent", "-noreactlogin"]
    if username:
        base_args += ["-login", username]
    cmd = [str(exe)] + base_args + (extra_args or [])
    log.info(f"Launching Steam: {' '.join(cmd)}")
    subprocess.Popen(cmd, close_fds=True)


def quit_steam(steam_exe_path: Optional[str] = None) -> None:
    """
    Gracefully exit Steam via the steam://exit URI, then wait for the process
    to terminate. Falls back to SIGTERM if Steam doesn't close within 30 s.

    Args:
        steam_exe_path: Path to steam.exe.  On Windows ``steam`` is not on
            PATH, so the full executable path is required for a graceful
            shutdown.  If omitted the graceful step is skipped and Steam is
            force-killed after the 30 s deadline.
    """
    log.info("Requesting Steam shutdown via steam://exit")
    if steam_exe_path:
        try:
            subprocess.Popen([steam_exe_path, "steam://exit"], close_fds=True)
        except (FileNotFoundError, OSError) as exc:
            log.warning(f"Could not send steam://exit via '{steam_exe_path}': {exc} — will force-kill.")
    else:
        log.warning("steam_exe_path not provided — skipping graceful shutdown, will force-kill.")

    deadline = time.time() + 30
    while time.time() < deadline:
        if not is_steam_running():
            log.info("Steam exited cleanly.")
            return
        time.sleep(2)

    # Force-kill if still running
    log.warning("Steam did not exit gracefully — force-killing.")
    _kill_processes(_STEAM_PROCESS_NAMES)


def is_steam_running() -> bool:
    """Return True if any Steam process is active."""
    return any(
        proc.name().lower() in _STEAM_PROCESS_NAMES
        for proc in psutil.process_iter(["name"])
    )


def wait_for_steam_ready(timeout_sec: int = 60, stabilise_sec: int = 15) -> bool:
    """
    Block until Steam's main process is detected and has had time to log in,
    or until *timeout_sec* elapses.

    Steam's process appears almost immediately after launch, but the client
    needs additional time to authenticate and become ready to launch games.
    ``stabilise_sec`` is an extra fixed wait applied *after* the process is
    first detected to cover that login window.

    Args:
        timeout_sec: Maximum time to wait for the process to appear.
        stabilise_sec: Extra seconds to wait after the process is detected
            so Steam can finish logging in before we try to launch TF2.
            Default 15 s — increase to 25-30 s on slow machines or HDD.

    Returns:
        True if Steam became ready within the timeout, False otherwise.
    """
    log.info(f"Waiting for Steam to initialise (timeout={timeout_sec}s)…")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if is_steam_running():
            log.info(f"Steam process detected — waiting {stabilise_sec}s for login to complete…")
            time.sleep(stabilise_sec)
            log.info("Steam is ready.")
            return True
        time.sleep(3)
    log.error("Timed out waiting for Steam to start.")
    return False


# ---------------------------------------------------------------------------
# Internal utility
# ---------------------------------------------------------------------------

def _kill_processes(names: set[str]) -> None:
    """Kill all processes whose name (lower-cased) is in *names*."""
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if proc.info["name"].lower() in names:
                log.debug(f"Killing process {proc.info['name']} (PID {proc.info['pid']})")
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass