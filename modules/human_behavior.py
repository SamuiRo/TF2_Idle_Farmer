"""
Human-behaviour simulation.

Responsibilities:
- Randomised sleep intervals that mimic natural user pauses
- Smooth Bézier-curve mouse movement
- Occasional random key presses
- Full idle-session loop with periodic micro-activity
- MOTD dismissal after server connect
"""

import random
import time
from typing import Callable

import pyautogui

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
    The death-drop notification window does NOT need to be closed — it does
    not block the drop timer and disappears on its own.

    Args:
        attempts:    How many times to repeat the key sequence.
                     Default 8 covers servers with up to 3-4 stacked MOTDs.
        interval_sec: Seconds to wait between attempts.
                     Default 4 s gives each MOTD panel time to appear.
    """
    log.info(f"Dismissing MOTD — {attempts} attempts, {interval_sec}s apart…")

    for i in range(attempts):
        try:
            # Send Enter multiple times first — it's the most universal confirm key
            for _ in range(4):
                pyautogui.press("return")
                time.sleep(0.2)

            # Space and F as fallbacks for servers using different bindings
            pyautogui.press("space")
            time.sleep(0.2)
            pyautogui.press("f")
            time.sleep(0.2)

            log.debug(f"MOTD dismiss attempt {i + 1}/{attempts} sent.")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"MOTD dismiss key press failed (non-critical): {exc}")

        if i < attempts - 1:
            time.sleep(interval_sec)

    log.info("MOTD dismiss sequence complete.")


def idle_session(duration_minutes: float) -> None:
    """
    Keep the process alive for *duration_minutes* while occasionally
    producing small human-like interactions (mouse moves / key presses).

    The session is split into variable-length sleep windows (3–10 min each).
    At the end of each window there is a 40 % chance of a micro-action.

    Args:
        duration_minutes: Total idle time in minutes (float for precision).
    """
    total_seconds = duration_minutes * 60
    end_time = time.time() + total_seconds
    log.info(f"Idle session started — duration: {duration_minutes:.1f} min")

    actions: list[Callable[[], None]] = [
        move_mouse_naturally,
        random_key_press,
        _do_nothing,
    ]

    while time.time() < end_time:
        remaining = end_time - time.time()
        if remaining <= 0:
            break

        # Sleep window: 3–10 min, but never exceed remaining time
        sleep_time = min(random.uniform(180, 600), remaining)
        log.debug(f"Sleeping {sleep_time / 60:.1f} min (remaining: {remaining / 60:.1f} min)")
        time.sleep(sleep_time)

        if time.time() >= end_time:
            break

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

    Only safe, non-command keys are used (Escape, F5).
    """
    safe_keys = ["escape", "f5"]
    key = random.choice(safe_keys)
    try:
        pyautogui.press(key)
        log.debug(f"Key pressed: {key}")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"Key press skipped: {exc}")


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _do_nothing() -> None:
    """Placeholder action — intentionally idle."""
    log.debug("No action this cycle (intentional idle).")