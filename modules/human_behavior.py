"""
Human-behaviour simulation.

Responsibilities:
- Randomised sleep intervals that mimic natural user pauses
- Smooth Bézier-curve mouse movement (pyautogui — affects cursor position only,
  never sends keyboard input globally)
- Occasional random key presses sent DIRECTLY to the TF2 window via PostMessage
- Full idle-session loop with periodic micro-activity
- MOTD dismissal after server connect
- Item-drop notification dismissal during idle

Key-press architecture
----------------------
All keyboard input is routed through modules._win_input.press_key_in_tf2() /
press_keys_in_tf2(), which use WM_KEYDOWN/WM_KEYUP sent via PostMessage to the
TF2 window handle (class "Valve001").  This means:

  * Keys are delivered only to TF2 — not to whatever window the user has open.
  * The user's active window retains focus at all times.
  * Works correctly for TF2 in windowed (-sw) and borderless windowed modes.

Mouse movement still uses pyautogui.moveTo() because moving the cursor globally
is intentional (it mimics a human using the machine) and does not interfere with
typing or other apps.
"""

from __future__ import annotations

import random
import time
from typing import Callable

import pyautogui

from modules._win_input import press_key_in_tf2, press_keys_in_tf2
from modules.constants import (
    AFK_SAFE_KEYS,
    BEZIER_STEP_DELAY_MAX_SEC,
    BEZIER_STEP_DELAY_MIN_SEC,
    BEZIER_STEPS_MAX,
    BEZIER_STEPS_MIN,
    DROP_DISMISS_INTERVAL_SEC,
    DROP_DISMISS_KEY_DELAY_SEC,
    DROP_DISMISS_KEYS,
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
    SECONDS_PER_MINUTE,
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
    log.debug(f"Waiting {duration:.1f}s…")
    time.sleep(duration)


def dismiss_motd(
    attempts: int = MOTD_DISMISS_ATTEMPTS,
    interval_sec: float = MOTD_DISMISS_INTERVAL_SEC,
) -> None:
    """
    Dismiss the server MOTD / welcome screen that appears after connecting.

    Some servers stack multiple MOTD windows (news, rules, ads) so we repeat
    the key sequence several times with pauses between each attempt.

    Keys sent each attempt: Enter × ``MOTD_ENTER_REPEAT``, then the keys in
    ``MOTD_FALLBACK_KEYS`` — this covers all common MOTD implementations
    (HTML overlay, text panel, legacy dialog).

    All key presses are sent directly to the TF2 window via PostMessage and
    do NOT affect any other application the user may have open.

    The death-drop notification window does NOT need to be closed here — it
    does not block the drop timer.  Use :func:`dismiss_item_drop` for that.

    Args:
        attempts:    How many times to repeat the key sequence.
        interval_sec: Seconds to wait between attempts.
    """
    log.info(f"Dismissing MOTD — {attempts} attempts, {interval_sec}s apart…")

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


def dismiss_item_drop() -> None:
    """
    Dismiss the item-drop notification popup that TF2 shows when a weekly
    drop is received.

    TF2 displays a small floating notification in-game when an item drops.
    The keys defined in ``DROP_DISMISS_KEYS`` close it.  The function sends
    them directly to the TF2 window so the popup does not linger on screen.

    It is safe to call this even when no popup is visible — the keys are
    harmless no-ops in that state.
    """
    log.debug("Sending item-drop dismiss keys to TF2 window…")
    try:
        press_keys_in_tf2(DROP_DISMISS_KEYS, inter_key_delay=DROP_DISMISS_KEY_DELAY_SEC)
        log.debug("Item-drop dismiss keys sent.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Item-drop dismiss failed (non-critical): {exc}")


def idle_session(duration_minutes: float, mouse_activity: bool = True) -> None:
    """
    Keep the process alive for *duration_minutes* while occasionally
    producing small human-like interactions (mouse moves / key presses).

    The session is split into variable-length sleep windows.  At the end of
    each window there is a chance (``IDLE_ACTION_PROBABILITY``) of a
    micro-action.

    Additionally, every ``DROP_DISMISS_INTERVAL_SEC`` seconds the function
    sends a quick dismiss sequence to the TF2 window to close any item-drop
    popup that may have appeared.

    Args:
        duration_minutes: Total idle time in minutes.
        mouse_activity:   When False, mouse movement is skipped entirely.
                          Controlled by ``[behavior] mouse_activity`` in
                          settings.toml.  Defaults to True.
    """
    total_seconds = duration_minutes * SECONDS_PER_MINUTE
    end_time = time.time() + total_seconds
    last_drop_dismiss = time.time()

    log.info(
        f"Idle session started — duration: {duration_minutes:.1f} min"
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

        # Sleep window: bounded by remaining time and the drop-dismiss
        # interval so the popup is dismissed in a timely manner.
        max_sleep = (
            min(DROP_DISMISS_INTERVAL_SEC, remaining)
            if DROP_DISMISS_INTERVAL_SEC > 0
            else remaining
        )
        sleep_time = min(
            random.uniform(IDLE_SLEEP_MIN_SEC, IDLE_SLEEP_MAX_SEC),
            max_sleep,
        )
        log.debug(
            f"Sleeping {sleep_time / 60:.1f} min "
            f"(remaining: {remaining / 60:.1f} min)"
        )
        time.sleep(sleep_time)

        if time.time() >= end_time:
            break

        # --- Periodic item-drop dismiss ---
        if (
            DROP_DISMISS_INTERVAL_SEC > 0
            and time.time() - last_drop_dismiss >= DROP_DISMISS_INTERVAL_SEC
        ):
            dismiss_item_drop()
            last_drop_dismiss = time.time()

        # --- Random micro-action ---
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
    Move the mouse to a random screen position along a quadratic Bézier curve
    to mimic organic, non-linear cursor movement.

    Mouse movement is intentionally global (it is meant to look like a human
    is at the machine) and does not send any keyboard input.
    """
    try:
        start_x, start_y = pyautogui.position()
        screen_w, screen_h = pyautogui.size()

        margin = MOUSE_SCREEN_MARGIN_PX
        ctrl_margin = MOUSE_CTRL_POINT_MARGIN_PX

        end_x = random.randint(margin, max(margin, screen_w - margin))
        end_y = random.randint(margin, max(margin, screen_h - margin))

        # Random control point for the quadratic Bézier curve
        ctrl_x = random.randint(ctrl_margin, max(ctrl_margin, screen_w - ctrl_margin))
        ctrl_y = random.randint(ctrl_margin, max(ctrl_margin, screen_h - ctrl_margin))

        steps = random.randint(BEZIER_STEPS_MIN, BEZIER_STEPS_MAX)

        for i in range(steps + 1):
            t = i / steps
            # Quadratic Bézier: B(t) = (1-t)²P0 + 2(1-t)t·Pc + t²P1
            x = int((1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * end_x)
            y = int((1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * end_y)
            pyautogui.moveTo(x, y, duration=0)
            time.sleep(
                random.uniform(BEZIER_STEP_DELAY_MIN_SEC, BEZIER_STEP_DELAY_MAX_SEC)
            )

        log.debug(f"Mouse moved to ({end_x}, {end_y}) via Bézier curve.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Mouse move skipped: {exc}")


def random_key_press() -> None:
    """
    Press a single harmless key to prevent AFK detection on servers that
    watch for keyboard inactivity.

    Keys are sent directly to the TF2 window via PostMessage — they do NOT
    affect any other window the user may have focused.

    Only safe, non-command keys defined in ``AFK_SAFE_KEYS`` are used.
    """
    key = random.choice(AFK_SAFE_KEYS)
    try:
        ok = press_key_in_tf2(key)
        if ok:
            log.debug(f"Key '{key}' sent to TF2 window.")
        else:
            log.debug(f"Key '{key}' skipped — TF2 window not available.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Key press skipped: {exc}")


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _do_nothing() -> None:
    """Placeholder action — intentionally idle."""
    log.debug("No action this cycle (intentional idle).")