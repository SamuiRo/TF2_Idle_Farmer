"""
Low-level Windows input routed directly to the TF2 window.

Uses PostMessage keyboard and mouse messages so input is delivered only to the
TF2 process — the user's active window retains focus at all times.

Public API
----------
press_key_in_tf2(key, delay_after=0.0)  -> bool
press_keys_in_tf2(keys, inter_key_delay=0.15) -> bool
click_in_tf2_client(x, y, delay_after=0.0) -> bool

Key names follow the pyautogui convention (e.g. "return", "escape", "space",
"f5", "f").  A lookup table maps them to Virtual-Key codes.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = wintypes.BOOL
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ClientToScreen.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.SetCursorPos.restype = wintypes.BOOL
    _WINDOWS_AVAILABLE = True
except (AttributeError, OSError):
    _WINDOWS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Windows message / VK constants
# ---------------------------------------------------------------------------

WM_KEYDOWN: int = 0x0100
WM_KEYUP: int = 0x0101
WM_MOUSEMOVE: int = 0x0200
WM_LBUTTONDOWN: int = 0x0201
WM_LBUTTONUP: int = 0x0202
MK_LBUTTON: int = 0x0001
MOUSEEVENTF_LEFTDOWN: int = 0x0002
MOUSEEVENTF_LEFTUP: int = 0x0004
MAPVK_VK_TO_VSC: int = 0

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
# Public data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TF2WindowInfo:
    """Geometry for the TF2 top-level window and its client area."""

    hwnd: int
    left: int
    top: int
    right: int
    bottom: int
    client_left: int
    client_top: int
    client_width: int
    client_height: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        return self.client_left + x, self.client_top + y


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_tf2_hwnd() -> int | None:
    """Return the HWND of the TF2 window, or None if not found."""
    if not _WINDOWS_AVAILABLE:
        return None
    hwnd = _user32.FindWindowW(_TF2_WINDOW_CLASS, None)
    return int(hwnd) if hwnd else None


def _get_cursor_pos() -> tuple[int, int] | None:
    """Return the current cursor position, or None if unavailable."""
    if not _WINDOWS_AVAILABLE:
        return None
    point = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        return None
    return int(point.x), int(point.y)


def _post_key(hwnd: int, vk: int) -> None:
    """Send WM_KEYDOWN then WM_KEYUP for *vk* to *hwnd*."""
    scan_code = int(_user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    lparam_down = 1 | (scan_code << 16)
    lparam_up = 1 | (scan_code << 16) | (1 << 30) | (1 << 31)
    _user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam_down)
    _user32.PostMessageW(hwnd, WM_KEYUP,   vk, lparam_up)


def _make_mouse_lparam(x: int, y: int) -> int:
    """Pack client-area x/y coordinates into a Windows mouse-message LPARAM."""
    return (y & 0xFFFF) << 16 | (x & 0xFFFF)


def _post_mouse_click(hwnd: int, x: int, y: int) -> bool:
    """Post a left mouse click to *hwnd* at client-area coordinate *(x, y)*."""
    lparam = _make_mouse_lparam(x, y)
    moved = bool(_user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam))
    down = bool(_user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam))
    time.sleep(0.03)
    up = bool(_user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam))
    return moved and down and up


def _click_with_os_mouse(
    info: TF2WindowInfo,
    x: int,
    y: int,
    restore_cursor: bool,
    foreground: bool,
) -> bool:
    """Legacy fallback: move the real cursor and click by screen coordinates."""
    old_pos = _get_cursor_pos() if restore_cursor else None
    screen_x, screen_y = info.client_to_screen(x, y)

    if foreground:
        _user32.SetForegroundWindow(info.hwnd)
        time.sleep(0.05)

    if not _user32.SetCursorPos(screen_x, screen_y):
        return False

    time.sleep(0.03)
    _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    if old_pos is not None:
        time.sleep(0.03)
        _user32.SetCursorPos(old_pos[0], old_pos[1])

    log.debug(
        "_win_input: Legacy OS mouse click at TF2 client coordinate "
        f"({x}, {y}) -> screen ({screen_x}, {screen_y})."
    )
    return True


def _resolve_vk(key: str) -> int | None:
    """Return the VK code for a pyautogui-style key name, or None."""
    return _VK_MAP.get(key.lower())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_tf2_window_info() -> TF2WindowInfo | None:
    """
    Return TF2 window geometry, including client-area screen origin.

    Coordinates used by game UI should normally be relative to the client area,
    not the full window including borders/title bars.
    """
    if not _WINDOWS_AVAILABLE:
        log.debug("_win_input: Windows API unavailable - no TF2 geometry.")
        return None

    hwnd = _find_tf2_hwnd()
    if hwnd is None:
        log.debug("_win_input: TF2 window not found - no geometry.")
        return None

    window_rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        log.debug("_win_input: GetWindowRect failed for TF2 window.")
        return None

    client_rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
        log.debug("_win_input: GetClientRect failed for TF2 window.")
        return None

    client_origin = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(client_origin)):
        log.debug("_win_input: ClientToScreen failed for TF2 window.")
        return None

    info = TF2WindowInfo(
        hwnd=hwnd,
        left=int(window_rect.left),
        top=int(window_rect.top),
        right=int(window_rect.right),
        bottom=int(window_rect.bottom),
        client_left=int(client_origin.x),
        client_top=int(client_origin.y),
        client_width=int(client_rect.right - client_rect.left),
        client_height=int(client_rect.bottom - client_rect.top),
    )
    log.debug(
        "_win_input: TF2 window geometry: "
        f"HWND={info.hwnd}, window={info.width}x{info.height}, "
        f"client={info.client_width}x{info.client_height} "
        f"at ({info.client_left}, {info.client_top})."
    )
    return info

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


def click_in_tf2_client(
    x: int,
    y: int,
    delay_after: float = 0.0,
    restore_cursor: bool = True,
    foreground: bool = False,
    fallback_to_os_click: bool = False,
) -> bool:
    """
    Click a client-area coordinate inside the TF2 window.

    By default this posts mouse messages directly to the TF2 HWND, so the real
    system cursor is not moved and the click is not sent to the foreground app.
    The legacy OS-click path is available only when *fallback_to_os_click* is
    explicitly enabled for troubleshooting.
    """
    if not _WINDOWS_AVAILABLE:
        log.debug("_win_input: Windows API unavailable - mouse click skipped.")
        return False

    info = get_tf2_window_info()
    if info is None:
        log.debug("_win_input: TF2 window not found - mouse click skipped.")
        return False

    if x < 0 or y < 0 or x >= info.client_width or y >= info.client_height:
        log.warning(
            "_win_input: TF2 click coordinate outside client area: "
            f"({x}, {y}) for {info.client_width}x{info.client_height}."
        )
        return False

    if _post_mouse_click(info.hwnd, x, y):
        log.debug(
            "_win_input: Posted TF2 mouse click at client coordinate "
            f"({x}, {y}) -> HWND {info.hwnd}."
        )
    elif fallback_to_os_click:
        log.debug(
            "_win_input: Window-message click failed; trying legacy OS mouse fallback."
        )
        if not _click_with_os_mouse(info, x, y, restore_cursor, foreground):
            log.debug("_win_input: Legacy OS mouse fallback failed.")
            return False
    else:
        log.debug("_win_input: TF2 window-message mouse click failed.")
        return False

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
