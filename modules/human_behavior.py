"""
Human-behaviour simulation.

Responsibilities:
- Randomised sleep intervals that mimic natural user pauses
- Smooth Bezier-curve mouse movement
- Occasional harmless key presses routed directly to the TF2 window
- MOTD dismissal after server connect
- Optional item-drop popup dismissal without blind Enter presses
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Mapping

import pyautogui

from modules._win_input import (
    click_in_tf2_client,
    get_tf2_window_info,
    press_key_in_tf2,
    press_keys_in_tf2,
)
from modules.constants import (
    AFK_SAFE_KEYS,
    BEZIER_STEP_DELAY_MAX_SEC,
    BEZIER_STEP_DELAY_MIN_SEC,
    BEZIER_STEPS_MAX,
    BEZIER_STEPS_MIN,
    DROP_DISMISS_INTERVAL_SEC,
    DROP_DISMISS_KEY_DELAY_SEC,
    DROP_DISMISS_KEYS,
    DROP_POPUP_CLICK_X,
    DROP_POPUP_CLICK_Y,
    DROP_POPUP_DETECT_MIN_CONTRAST,
    DROP_POPUP_DETECT_MIN_ORANGE_RATIO,
    DROP_POPUP_DETECT_REGION,
    DROP_POPUP_DISMISS_MODE_DEFAULT,
    IDLE_ACTION_PROBABILITY,
    IDLE_SLEEP_MAX_SEC,
    IDLE_SLEEP_MIN_SEC,
    MOTD_DISMISS_ATTEMPTS,
    MOTD_DISMISS_INTERVAL_SEC,
    MOTD_ENTER_REPEAT,
    MOTD_FALLBACK_KEYS,
    MOTD_KEY_DELAY_SEC,
    MOUSE_CTRL_POINT_MARGIN_PX,
    MOUSE_SCREEN_MARGIN_PX,
)
from modules.logger import log


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def wait(min_sec: float, max_sec: float) -> None:
    """
    Sleep for a uniformly random duration in [min_sec, max_sec].

    Args:
        min_sec: Lower bound in seconds.
        max_sec: Upper bound in seconds.
    """
    duration = random.uniform(min_sec, max_sec)
    log.debug(f"Waiting {duration:.1f}s...")
    time.sleep(duration)


def dismiss_motd(
    attempts: int = MOTD_DISMISS_ATTEMPTS,
    interval_sec: float = MOTD_DISMISS_INTERVAL_SEC,
) -> None:
    """
    Dismiss the server MOTD / welcome screen that appears after connecting.

    MOTD remains keyboard-driven because it is a startup-only server UI panel.
    Drop popups are handled separately to avoid blind Enter presses during idle.
    """
    log.info(f"Dismissing MOTD - {attempts} attempts, {interval_sec}s apart...")

    for i in range(attempts):
        try:
            for _ in range(MOTD_ENTER_REPEAT):
                press_key_in_tf2("return", delay_after=MOTD_KEY_DELAY_SEC)

            for key in MOTD_FALLBACK_KEYS:
                press_key_in_tf2(key, delay_after=MOTD_KEY_DELAY_SEC)

            log.debug(f"MOTD dismiss attempt {i + 1}/{attempts} sent.")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"MOTD dismiss key press failed (non-critical): {exc}")

        if i < attempts - 1:
            time.sleep(interval_sec)

    log.info("MOTD dismiss sequence complete.")


def dismiss_item_drop(
    drop_popup_config: Mapping[str, Any] | None = None,
) -> None:
    """
    Dismiss the item-drop notification popup if configured to do so.

    The default "auto" mode uses a small screenshot heuristic and then clicks a
    TF2 client-area coordinate. This avoids periodic Enter/Escape presses, which
    can interact with server votes or other menus.
    """
    mode = _get_drop_popup_mode(drop_popup_config)
    if mode == "off":
        log.debug("Item-drop popup dismissal disabled.")
        return

    try:
        if mode == "keyboard_fallback":
            log.debug("Sending item-drop dismiss keys to TF2 window.")
            press_keys_in_tf2(
                DROP_DISMISS_KEYS,
                inter_key_delay=DROP_DISMISS_KEY_DELAY_SEC,
            )
            log.debug("Item-drop dismiss keys sent.")
            return

        if mode not in {"auto", "mouse"}:
            log.warning(f"Unknown drop_popup_dismiss mode '{mode}' - skipping.")
            return

        if mode == "auto" and not _looks_like_drop_popup(drop_popup_config):
            log.debug("Drop-popup detector did not match - no click sent.")
            return

        click_x = _get_int_setting(
            drop_popup_config,
            "drop_popup_click_x",
            DROP_POPUP_CLICK_X,
        )
        click_y = _get_int_setting(
            drop_popup_config,
            "drop_popup_click_y",
            DROP_POPUP_CLICK_Y,
        )
        ok = click_in_tf2_client(click_x, click_y, delay_after=0.10)
        if ok:
            log.debug(f"Item-drop popup mouse dismiss sent at ({click_x}, {click_y}).")
        else:
            log.debug("Item-drop popup mouse dismiss skipped - TF2 click failed.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Item-drop dismiss failed (non-critical): {exc}")


def idle_session(
    duration_minutes: float,
    mouse_activity: bool = True,
    drop_popup_config: Mapping[str, Any] | None = None,
) -> None:
    """
    Keep the process alive while occasionally producing small interactions.

    Item-drop popup dismissal is configured via the behavior section. The safe
    default is "auto": screenshot a small UI region and click only if it looks
    like a centered TF2 modal/drop popup.
    """
    total_seconds = duration_minutes * 60
    end_time = time.time() + total_seconds
    last_drop_dismiss = time.time()
    drop_dismiss_interval = _get_float_setting(
        drop_popup_config,
        "drop_popup_check_interval_sec",
        float(DROP_DISMISS_INTERVAL_SEC),
    )

    log.info(
        f"Idle session started - duration: {duration_minutes:.1f} min"
        f"{'' if mouse_activity else ' (mouse movement disabled)'}"
    )

    actions: list[Callable[[], None]] = (
        [move_mouse_naturally, random_key_press, _do_nothing]
        if mouse_activity
        else [random_key_press, _do_nothing]
    )

    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break

        max_sleep = (
            min(drop_dismiss_interval, remaining)
            if drop_dismiss_interval > 0
            else remaining
        )
        sleep_time = min(random.uniform(IDLE_SLEEP_MIN_SEC, IDLE_SLEEP_MAX_SEC), max_sleep)
        log.debug(
            f"Sleeping {sleep_time / 60:.1f} min "
            f"(remaining: {remaining / 60:.1f} min)"
        )
        time.sleep(sleep_time)

        if time.time() >= end_time:
            break

        if (
            drop_dismiss_interval > 0
            and time.time() - last_drop_dismiss >= drop_dismiss_interval
        ):
            dismiss_item_drop(drop_popup_config)
            last_drop_dismiss = time.time()

        if random.random() < IDLE_ACTION_PROBABILITY:
            action = random.choice(actions)
            log.debug(f"Performing micro-action: {action.__name__}")
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                log.warning(f"Micro-action failed (non-critical): {exc}")

    log.info("Idle session finished.")


def move_mouse_naturally() -> None:
    """
    Move the mouse to a random screen position along a quadratic Bezier curve.
    """
    try:
        start_x, start_y = pyautogui.position()
        screen_w, screen_h = pyautogui.size()

        end_x = random.randint(
            MOUSE_SCREEN_MARGIN_PX,
            max(MOUSE_SCREEN_MARGIN_PX, screen_w - MOUSE_SCREEN_MARGIN_PX),
        )
        end_y = random.randint(
            MOUSE_SCREEN_MARGIN_PX,
            max(MOUSE_SCREEN_MARGIN_PX, screen_h - MOUSE_SCREEN_MARGIN_PX),
        )

        ctrl_x = random.randint(
            MOUSE_CTRL_POINT_MARGIN_PX,
            max(MOUSE_CTRL_POINT_MARGIN_PX, screen_w - MOUSE_CTRL_POINT_MARGIN_PX),
        )
        ctrl_y = random.randint(
            MOUSE_CTRL_POINT_MARGIN_PX,
            max(MOUSE_CTRL_POINT_MARGIN_PX, screen_h - MOUSE_CTRL_POINT_MARGIN_PX),
        )

        steps = random.randint(BEZIER_STEPS_MIN, BEZIER_STEPS_MAX)

        for i in range(steps + 1):
            t = i / steps
            x = int((1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t**2 * end_x)
            y = int((1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t**2 * end_y)
            pyautogui.moveTo(x, y, duration=0)
            time.sleep(random.uniform(BEZIER_STEP_DELAY_MIN_SEC, BEZIER_STEP_DELAY_MAX_SEC))

        log.debug(f"Mouse moved to ({end_x}, {end_y}) via Bezier curve.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Mouse move skipped: {exc}")


def random_key_press() -> None:
    """
    Press a single harmless key to prevent AFK detection on some servers.
    """
    key = random.choice(AFK_SAFE_KEYS)
    try:
        ok = press_key_in_tf2(key)
        if ok:
            log.debug(f"Key '{key}' sent to TF2 window.")
        else:
            log.debug(f"Key '{key}' skipped - TF2 window not available.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Key press skipped: {exc}")


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _do_nothing() -> None:
    """Placeholder action - intentionally idle."""
    log.debug("No action this cycle (intentional idle).")


def _get_drop_popup_mode(config: Mapping[str, Any] | None) -> str:
    value = (config or {}).get("drop_popup_dismiss", DROP_POPUP_DISMISS_MODE_DEFAULT)
    return str(value).strip().lower()


def _get_int_setting(config: Mapping[str, Any] | None, key: str, default: int) -> int:
    try:
        return int((config or {}).get(key, default))
    except (TypeError, ValueError):
        log.warning(f"Invalid integer setting '{key}', using default {default}.")
        return default


def _get_float_setting(config: Mapping[str, Any] | None, key: str, default: float) -> float:
    try:
        return float((config or {}).get(key, default))
    except (TypeError, ValueError):
        log.warning(f"Invalid numeric setting '{key}', using default {default}.")
        return default


def _get_detect_region(config: Mapping[str, Any] | None) -> tuple[int, int, int, int]:
    raw = (config or {}).get("drop_popup_detect_region", DROP_POPUP_DETECT_REGION)
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            x, y, width, height = (int(v) for v in raw)
            return x, y, width, height
        except (TypeError, ValueError):
            pass
    log.warning(
        "Invalid drop_popup_detect_region, using default "
        f"{DROP_POPUP_DETECT_REGION}."
    )
    return DROP_POPUP_DETECT_REGION


def _looks_like_drop_popup(config: Mapping[str, Any] | None) -> bool:
    """
    Conservative screenshot heuristic for a centered TF2 modal/drop popup.

    It samples a small region and looks for enough contrast plus TF2-style
    orange/yellow UI color. It is deliberately simple and cheap.
    """
    info = get_tf2_window_info()
    if info is None:
        return False

    x, y, width, height = _get_detect_region(config)
    x = max(0, min(x, info.client_width - 1))
    y = max(0, min(y, info.client_height - 1))
    width = max(1, min(width, info.client_width - x))
    height = max(1, min(height, info.client_height - y))

    try:
        screenshot = pyautogui.screenshot(
            region=(info.client_left + x, info.client_top + y, width, height)
        ).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        log.debug(f"Drop-popup screenshot failed: {exc}")
        return False

    pixels = list(screenshot.getdata())
    if not pixels:
        return False

    step = max(1, len(pixels) // 5000)
    sample = pixels[::step]
    luma_values = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in sample]
    contrast = max(luma_values) - min(luma_values)
    orange_count = sum(
        1
        for r, g, b in sample
        if r >= 120 and 55 <= g <= 190 and b <= 100 and r >= g * 0.9
    )
    orange_ratio = orange_count / len(sample)

    min_contrast = _get_float_setting(
        config,
        "drop_popup_detect_min_contrast",
        DROP_POPUP_DETECT_MIN_CONTRAST,
    )
    min_orange_ratio = _get_float_setting(
        config,
        "drop_popup_detect_min_orange_ratio",
        DROP_POPUP_DETECT_MIN_ORANGE_RATIO,
    )

    matched = contrast >= min_contrast and orange_ratio >= min_orange_ratio
    log.debug(
        "Drop-popup detector: "
        f"contrast={contrast:.1f}/{min_contrast:.1f}, "
        f"orange={orange_ratio:.3f}/{min_orange_ratio:.3f}, "
        f"matched={matched}."
    )
    return matched
