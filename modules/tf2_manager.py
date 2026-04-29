"""
TF2 process management.

Responsibilities:
- Generate autoexec.cfg with performance tweaks and server connect command
- Launch TF2 with minimal-resource launch options
- Wait for the game to be ready
- Quit / kill TF2
"""

from __future__ import annotations

import random
import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil

from modules.constants import (
    AUTOEXEC_TEMPLATE,
    TF2_APP_ID,
    TF2_LAUNCH_OPTIONS,
    TF2_PROCESS_NAMES,
    TF2_START_POLL_INTERVAL_SEC,
)
from modules.logger import log


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_autoexec(server_ip_port: str, tf2_cfg_dir: str) -> Path:
    """
    Write a fresh autoexec.cfg that points at *server_ip_port*.

    Args:
        server_ip_port: e.g. ``"103.28.54.100:27015"``
        tf2_cfg_dir:    Path to the TF2 cfg directory.

    Returns:
        Path to the written file.
    """
    cfg_path = Path(tf2_cfg_dir) / "autoexec.cfg"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    content = AUTOEXEC_TEMPLATE.format(server=server_ip_port)
    cfg_path.write_text(content, encoding="utf-8")
    log.info(f"autoexec.cfg generated → connect {server_ip_port}")
    return cfg_path


def launch_tf2(steam_exe_path: str, extra_options: Optional[list[str]] = None) -> None:
    """
    Launch TF2 via Steam with the minimal-resource launch options.

    Args:
        steam_exe_path: Path to steam.exe (needed to pass ``-applaunch``).
        extra_options:  Any additional launch parameters to append.
    """
    options = TF2_LAUNCH_OPTIONS + (extra_options or [])
    cmd = [steam_exe_path, "-applaunch", TF2_APP_ID] + options
    log.info(f"Launching TF2: {' '.join(cmd)}")
    subprocess.Popen(cmd, close_fds=True)


def wait_for_tf2_ready(timeout_sec: int = 90) -> bool:
    """
    Block until hl2.exe / tf_win64.exe appears or *timeout_sec* elapses.

    Returns:
        True if TF2 started within the timeout, False otherwise.
    """
    log.info(f"Waiting for TF2 to initialise (timeout={timeout_sec}s)…")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if is_tf2_running():
            log.info("TF2 process detected — game is starting.")
            return True
        time.sleep(TF2_START_POLL_INTERVAL_SEC)

    log.error("Timed out waiting for TF2.")
    return False


def quit_tf2() -> None:
    """
    Force-kill all TF2 processes.

    A clean-shutdown approach (e.g. RCON ``quit``) would be more elegant but
    adds complexity.  For a windowless idle session, process termination is
    acceptable.
    """
    log.info("Requesting TF2 shutdown…")
    _kill_tf2_processes()


def is_tf2_running() -> bool:
    """Return True if a TF2 process is currently active."""
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"].lower() in TF2_PROCESS_NAMES:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def get_random_server(servers: list[str]) -> str:
    """
    Return a randomly chosen server from *servers*.

    Args:
        servers: List of ``"IP:PORT"`` strings.

    Returns:
        One server string chosen at random.

    Raises:
        ValueError: If the list is empty.
    """
    if not servers:
        raise ValueError("Server list is empty — cannot pick a random server.")
    choice = random.choice(servers)
    log.debug(f"Selected server: {choice}")
    return choice


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _kill_tf2_processes() -> None:
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if proc.info["name"].lower() in TF2_PROCESS_NAMES:
                log.info(
                    f"Killing {proc.info['name']} (PID {proc.info['pid']})"
                )
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass