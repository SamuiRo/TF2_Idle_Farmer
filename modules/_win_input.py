"""
Low-level Windows keyboard input routed directly to the TF2 window.

Uses PostMessage(WM_KEYDOWN / WM_KEYUP) so keys are delivered only to the
TF2 process — the user's active window retains focus at all times.

Public API
----------
press_key_in_tf2(key, delay_after=0.0)  -> bool
press_keys_in_tf2(keys, inter_key_delay=0.15) -> bool

Key names follow the pyautogui convention (e.g. "return", "escape", "space",
"f5", "f").  A lookup table maps them to Virtual-Key codes.
"""

from __future__ import annotations

import time
from typing import Sequence

from modules.logger import log

# ---------------------------------------------------------------------------
# Optional Windows-only imports
# ---------------------------------------------------------------------------
try:
    import ctypes
    import ctypes.wintypes as wintypes

    _user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    _WINDOWS_AVAILABLE = True
except (AttributeError, OSError):
    _WINDOWS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Windows message / VK constants
# ---------------------------------------------------------------------------

WM_KEYDOWN: int = 0x0100
WM_KEYUP: int = 0x0101

# Virtual-Key code map — pyautogui key name → VK code
_VK_MAP: dict[str, int] = {
    "return":    0x0D,  # VK_RETURN
    "enter":     0x0D,
    "escape":    0x1B,  # VK_ESCAPE
    "space":     0x20,  # VK_SPACE
    "tab":       0x09,  # VK_TAB
    "f1":        0x70,
    "f2":        0x71,
    "f3":        0x72,
    "f4":        0x73,
    "f5":        0x74,
    "f6":        0x75,
    "f7":        0x76,
    "f8":        0x77,
    "f9":        0x78,
    "f10":       0x79,
    "f11":       0x7A,
    "f12":       0x7B,
    # Letters a-z  (VK codes are the upper-case ASCII values)
    **{chr(c): ord(chr(c).upper()) for c in range(ord("a"), ord("z") + 1)},
    # Digits 0-9
    **{str(d): ord(str(d)) for d in range(10)},
}

# TF2 window class name (constant across all TF2 builds)
_TF2_WINDOW_CLASS: str = "Valve001"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_tf2_hwnd() -> int | None:
    """Return the HWND of the TF2 window, or None if not found."""
    if not _WINDOWS_AVAILABLE:
        return None
    hwnd = _user32.FindWindowW(_TF2_WINDOW_CLASS, None)
    return int(hwnd) if hwnd else None


def _post_key(hwnd: int, vk: int) -> None:
    """Send WM_KEYDOWN then WM_KEYUP for *vk* to *hwnd*."""
    lparam_down = (1) | (vk << 16)           # repeat=1, scan code approximation
    lparam_up   = (1) | (vk << 16) | (1 << 30) | (1 << 31)  # prev=1, trans=1
    _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam_down)
    _user32.PostMessageW(hwnd, WM_KEYUP,   vk, lparam_up)


def _resolve_vk(key: str) -> int | None:
    """Return the VK code for a pyautogui-style key name, or None."""
    return _VK_MAP.get(key.lower())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def press_key_in_tf2(key: str, delay_after: float = 0.0) -> bool:
    """
    Send a single key press (down + up) directly to the TF2 window.

    Args:
        key:         pyautogui-compatible key name (e.g. "return", "escape").
        delay_after: Seconds to sleep after sending the key.

    Returns:
        True if the key was sent, False if TF2 window was not found or the
        key name is not in the VK map.
    """
    if not _WINDOWS_AVAILABLE:
        log.debug(f"_win_input: Windows API unavailable — skipping key '{key}'.")
        return False

    vk = _resolve_vk(key)
    if vk is None:
        log.warning(f"_win_input: Unknown key name '{key}' — skipped.")
        return False

    hwnd = _find_tf2_hwnd()
    if hwnd is None:
        log.debug("_win_input: TF2 window not found — key not sent.")
        return False

    _post_key(hwnd, vk)
    log.debug(f"_win_input: Sent key '{key}' (VK=0x{vk:02X}) → HWND {hwnd}.")

    if delay_after > 0:
        time.sleep(delay_after)

    return True


def press_keys_in_tf2(
    keys: Sequence[str],
    inter_key_delay: float = 0.15,
) -> bool:
    """
    Send multiple keys in sequence to the TF2 window.

    Args:
        keys:            Iterable of pyautogui-compatible key names.
        inter_key_delay: Seconds to wait between each key press.

    Returns:
        True if at least one key was sent successfully, False otherwise.
    """
    any_sent = False
    for key in keys:
        sent = press_key_in_tf2(key, delay_after=inter_key_delay)
        any_sent = any_sent or sent
    return any_sent