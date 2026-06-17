"""
Post-launch TF2 connection health checks.

The check is intentionally conservative:
- console.log failure lines are treated as authoritative
- a gray Source-engine dialog is only a fallback signal
- lack of a success line does not fail by default unless configured
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Any, Mapping

import pyautogui

from modules._win_input import get_tf2_window_info
from modules.constants import (
    TF2_CONNECTION_CHECK_ENABLED_DEFAULT,
    TF2_CONNECTION_CHECK_INTERVAL_SEC_DEFAULT,
    TF2_CONNECTION_CHECK_REQUIRE_SUCCESS_DEFAULT,
    TF2_CONNECTION_CHECK_TIMEOUT_SEC_DEFAULT,
    TF2_CONNECTION_DIALOG_DETECT_ENABLED_DEFAULT,
    TF2_CONNECTION_DIALOG_DETECT_REGION,
    TF2_CONNECTION_DIALOG_GRACE_SEC_DEFAULT,
    TF2_CONNECTION_DIALOG_MATCHES_REQUIRED_DEFAULT,
    TF2_CONNECTION_DIALOG_MIN_GRAY_RATIO_DEFAULT,
    TF2_CONNECTION_FAILURE_PATTERNS,
    TF2_CONNECTION_SUCCESS_PATTERNS,
)
from modules.logger import log


@dataclass(frozen=True)
class ConnectionCheckResult:
    """Outcome of the post-launch connection check."""

    ok: bool
    reason: str
    evidence: str | None = None


_SUCCESS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE) for pattern in TF2_CONNECTION_SUCCESS_PATTERNS
]
_FAILURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(pattern, re.IGNORECASE) for pattern in TF2_CONNECTION_FAILURE_PATTERNS
]


def check_tf2_connection(
    console_log_path: str,
    config: Mapping[str, Any] | None = None,
) -> ConnectionCheckResult:
    """
    Poll for signs that TF2 joined the server or failed to connect.

    Args:
        console_log_path: Fresh console.log path for this launch.
        config: Optional ``[connection_check]`` settings.

    Returns:
        ConnectionCheckResult. ``ok=False`` means the session should be skipped.
    """
    if not _get_bool(config, "enabled", TF2_CONNECTION_CHECK_ENABLED_DEFAULT):
        return ConnectionCheckResult(True, "Connection health check disabled.")

    timeout_sec = _get_float(
        config,
        "timeout_sec",
        TF2_CONNECTION_CHECK_TIMEOUT_SEC_DEFAULT,
        minimum=0.0,
    )
    interval_sec = _get_float(
        config,
        "interval_sec",
        TF2_CONNECTION_CHECK_INTERVAL_SEC_DEFAULT,
        minimum=1.0,
    )
    require_success = _get_bool(
        config,
        "require_success",
        TF2_CONNECTION_CHECK_REQUIRE_SUCCESS_DEFAULT,
    )
    dialog_enabled = _get_bool(
        config,
        "detect_failure_dialog",
        TF2_CONNECTION_DIALOG_DETECT_ENABLED_DEFAULT,
    )
    dialog_grace_sec = _get_float(
        config,
        "failure_dialog_grace_sec",
        TF2_CONNECTION_DIALOG_GRACE_SEC_DEFAULT,
        minimum=0.0,
    )
    dialog_matches_required = _get_int(
        config,
        "failure_dialog_matches_required",
        TF2_CONNECTION_DIALOG_MATCHES_REQUIRED_DEFAULT,
        minimum=1,
    )

    log.info(
        "Checking TF2 connection health "
        f"(timeout={timeout_sec:.0f}s, interval={interval_sec:.0f}s)."
    )

    deadline = time.time() + timeout_sec
    started_at = time.time()
    dialog_matches = 0
    latest_text = ""

    while time.time() <= deadline:
        latest_text = _read_console_log(console_log_path)

        failure = _find_first_match(_FAILURE_PATTERNS, latest_text)
        if failure is not None:
            return ConnectionCheckResult(
                False,
                "TF2 reported a connection failure in console.log.",
                failure,
            )

        success = _find_first_match(_SUCCESS_PATTERNS, latest_text)
        if success is not None:
            return ConnectionCheckResult(
                True,
                "TF2 connection success found in console.log.",
                success,
            )

        elapsed = time.time() - started_at
        if dialog_enabled and elapsed >= dialog_grace_sec:
            if _looks_like_failure_dialog(config):
                dialog_matches += 1
                log.debug(
                    "TF2 failure-dialog heuristic matched "
                    f"{dialog_matches}/{dialog_matches_required}."
                )
                if dialog_matches >= dialog_matches_required:
                    return ConnectionCheckResult(
                        False,
                        "Possible TF2 connection failure dialog detected.",
                        "center gray dialog heuristic matched repeatedly",
                    )
            else:
                dialog_matches = 0

        time.sleep(min(interval_sec, max(0.0, deadline - time.time())))

    if require_success:
        return ConnectionCheckResult(
            False,
            "Timed out waiting for a TF2 connection success signal.",
            _last_console_line(latest_text),
        )

    return ConnectionCheckResult(
        True,
        "No explicit TF2 connection failure detected before timeout.",
        _last_console_line(latest_text),
    )


def _read_console_log(console_log_path: str) -> str:
    path = Path(console_log_path)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.debug(f"Connection health check could not read console.log: {exc}")
        return ""


def _find_first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return match.group(0).strip()
    return None


def _looks_like_failure_dialog(config: Mapping[str, Any] | None) -> bool:
    info = get_tf2_window_info()
    if info is None:
        return False

    x, y, width, height = _get_region(config)
    x = max(0, min(x, info.client_width - 1))
    y = max(0, min(y, info.client_height - 1))
    width = max(1, min(width, info.client_width - x))
    height = max(1, min(height, info.client_height - y))

    try:
        screenshot = pyautogui.screenshot(
            region=(info.client_left + x, info.client_top + y, width, height)
        ).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        log.debug(f"TF2 connection dialog screenshot failed: {exc}")
        return False

    pixels = list(screenshot.getdata())
    if not pixels:
        return False

    step = max(1, len(pixels) // 5000)
    sample = pixels[::step]
    gray_count = 0
    luma_values: list[float] = []
    for r, g, b in sample:
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        luma_values.append(luma)
        if abs(r - g) <= 18 and abs(g - b) <= 18 and 70 <= luma <= 215:
            gray_count += 1

    gray_ratio = gray_count / len(sample)
    contrast = max(luma_values) - min(luma_values)
    min_gray_ratio = _get_float(
        config,
        "failure_dialog_min_gray_ratio",
        TF2_CONNECTION_DIALOG_MIN_GRAY_RATIO_DEFAULT,
        minimum=0.0,
    )

    matched = gray_ratio >= min_gray_ratio and contrast >= 25.0
    log.debug(
        "TF2 failure-dialog detector: "
        f"gray={gray_ratio:.3f}/{min_gray_ratio:.3f}, "
        f"contrast={contrast:.1f}, matched={matched}."
    )
    return matched


def _get_region(config: Mapping[str, Any] | None) -> tuple[int, int, int, int]:
    raw = (config or {}).get(
        "failure_dialog_detect_region",
        TF2_CONNECTION_DIALOG_DETECT_REGION,
    )
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            x, y, width, height = (int(value) for value in raw)
            return x, y, width, height
        except (TypeError, ValueError):
            pass
    log.warning(
        "Invalid failure_dialog_detect_region, using default "
        f"{TF2_CONNECTION_DIALOG_DETECT_REGION}."
    )
    return TF2_CONNECTION_DIALOG_DETECT_REGION


def _last_console_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:300]
    return None


def _get_bool(config: Mapping[str, Any] | None, key: str, default: bool) -> bool:
    value = (config or {}).get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _get_float(
    config: Mapping[str, Any] | None,
    key: str,
    default: float,
    minimum: float | None = None,
) -> float:
    try:
        result = float((config or {}).get(key, default))
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


def _get_int(
    config: Mapping[str, Any] | None,
    key: str,
    default: int,
    minimum: int | None = None,
) -> int:
    try:
        result = int((config or {}).get(key, default))
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result
