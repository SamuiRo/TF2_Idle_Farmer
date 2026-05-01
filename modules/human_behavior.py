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

import random
import time
from typing import Callable

import pyautogui

from modules._win_input import press_key_in_tf2, press_keys_in_tf2
from modules.logger import log


# ---------------------------------------------------------------------------
# Configuration knobs (can be overridden at call-site)
# ---------------------------------------------------------------------------

# Probability that *some* action is performed each wakeup cycle (0–1)
_ACTION_PROBABILITY = 0.40

# Bézier curve resolution
_BEZIER_STEPS_MIN = 20
_BEZIER_STEPS_MAX = 40
_BEZIER_STEP_DELAY_MIN = 0.010  # seconds between each micro-move
_BEZIER_STEP_DELAY_MAX = 0.030

# How often to check for (and dismiss) item-drop notifications during idle.
# TF2 shows a small on-screen popup; pressing Enter or Escape closes it.
# Set to 0 to disable periodic dismiss checks.
_DROP_DISMISS_INTERVAL_SEC = 120  # every ~2 min


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


def dismiss_motd(attempts: int = 8, interval_sec: float = 4.0) -> None:
    """
    Dismiss the server MOTD / welcome screen that appears after connecting.

    Some servers stack multiple MOTD windows (news, rules, ads) so we repeat
    the key sequence several times with pauses between each attempt.

    Keys sent each attempt: Enter × 4, then Space, then F — this covers all
    common MOTD implementations (HTML overlay, text panel, legacy dialog).

    All key presses are sent directly to the TF2 window via PostMessage and
    do NOT affect any other application the user may have open.

    The death-drop notification window does NOT need to be closed here — it
    does not block the drop timer.  Use dismiss_item_drop() for that.

    Args:
        attempts:    How many times to repeat the key sequence.
                     Default 8 covers servers with up to 3-4 stacked MOTDs.
        interval_sec: Seconds to wait between attempts.
                     Default 4 s gives each MOTD panel time to appear.
    """
    log.info(f"Dismissing MOTD — {attempts} attempts, {interval_sec}s apart…")

    for i in range(attempts):
        try:
            # Enter × 4 is the most universal confirm key for MOTD panels
            for _ in range(4):
                press_key_in_tf2("return", delay_after=0.20)

            # Space and F as fallbacks for servers using non-standard bindings
            press_key_in_tf2("space", delay_after=0.20)
            press_key_in_tf2("f",     delay_after=0.20)

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
    Pressing Enter (or Escape) closes it.  This function sends both keys
    directly to the TF2 window so the popup does not linger on screen.

    It is safe to call this even when no popup is visible — the keys are
    harmless no-ops in that state.
    """
    log.debug("Sending item-drop dismiss keys to TF2 window…")
    try:
        press_keys_in_tf2(["return", "escape"], inter_key_delay=0.15)
        log.debug("Item-drop dismiss keys sent.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Item-drop dismiss failed (non-critical): {exc}")


def idle_session(duration_minutes: float, mouse_activity: bool = True) -> None:
    """
    Keep the process alive for *duration_minutes* while occasionally
    producing small human-like interactions (mouse moves / key presses).

    The session is split into variable-length sleep windows (3–10 min each).
    At the end of each window there is a 40 % chance of a micro-action.

    Additionally, every ~2 minutes the function sends a quick dismiss sequence
    to the TF2 window to close any item-drop popup that may have appeared.
    This is non-intrusive because the keys go only to TF2, not globally.

    Args:
        duration_minutes: Total idle time in minutes (float for precision).
        mouse_activity:   When False, mouse movement is skipped entirely.
                          Controlled by ``[behavior] mouse_activity`` in
                          settings.toml.  Defaults to True.
    """
    total_seconds = duration_minutes * 60
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

        # Sleep window: 3–10 min, but never exceed remaining time.
        # We wake up at least every _DROP_DISMISS_INTERVAL_SEC so the drop
        # popup is dismissed in a timely manner.
        max_sleep = (
            min(_DROP_DISMISS_INTERVAL_SEC, remaining)
            if _DROP_DISMISS_INTERVAL_SEC > 0
            else remaining
        )
        sleep_time = min(random.uniform(180, 600), max_sleep)
        log.debug(f"Sleeping {sleep_time / 60:.1f} min (remaining: {remaining / 60:.1f} min)")
        time.sleep(sleep_time)

        if time.time() >= end_time:
            break

        # --- Periodic item-drop dismiss ---
        if (
            _DROP_DISMISS_INTERVAL_SEC > 0
            and time.time() - last_drop_dismiss >= _DROP_DISMISS_INTERVAL_SEC
        ):
            dismiss_item_drop()
            last_drop_dismiss = time.time()

        # --- Random micro-action ---
        if random.random() < _ACTION_PROBABILITY:
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
    is at the machine).  It does not send any keyboard input.
    """
    try:
        start_x, start_y = pyautogui.position()
        screen_w, screen_h = pyautogui.size()

        end_x = random.randint(50, max(50, screen_w - 50))
        end_y = random.randint(50, max(50, screen_h - 50))

        # Random control point for the quadratic Bézier curve
        ctrl_x = random.randint(100, max(100, screen_w - 100))
        ctrl_y = random.randint(100, max(100, screen_h - 100))

        steps = random.randint(_BEZIER_STEPS_MIN, _BEZIER_STEPS_MAX)

        for i in range(steps + 1):
            t = i / steps
            # Quadratic Bézier: B(t) = (1-t)²P0 + 2(1-t)t·Pc + t²P1
            x = int((1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * end_x)
            y = int((1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * end_y)
            pyautogui.moveTo(x, y, duration=0)
            time.sleep(random.uniform(_BEZIER_STEP_DELAY_MIN, _BEZIER_STEP_DELAY_MAX))

        log.debug(f"Mouse moved to ({end_x}, {end_y}) via Bézier curve.")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Mouse move skipped: {exc}")


def random_key_press() -> None:
    """
    Press a single harmless key to prevent AFK detection on servers that watch
    for keyboard inactivity.

    Keys are sent directly to the TF2 window via PostMessage — they do NOT
    affect any other window the user may have focused.

    Only safe, non-command keys are used (Escape, F5).
    """
    safe_keys = ["escape", "f5"]
    key = random.choice(safe_keys)
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