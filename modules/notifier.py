"""
Best-effort Telegram and Discord notifications.

Configured via the optional [notifications] section in settings.toml.
Network failures are logged but never crash a farming run.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from modules.logger import log


def send_alert(
    settings: dict[str, Any],
    title: str,
    message: str,
) -> bool:
    """
    Send an alert to every configured notification target.

    Returns True if at least one target accepted the request.
    """
    config = settings.get("notifications", {})
    if not config:
        log.debug("Notifications not configured - alert skipped.")
        return False

    if not _get_bool(config.get("enabled", True)):
        log.debug("Notifications disabled - alert skipped.")
        return False

    timeout_sec = _get_float(config.get("timeout_sec", 10.0), default=10.0)
    delivered = False

    discord_webhook_url = str(config.get("discord_webhook_url") or "").strip()
    if discord_webhook_url:
        delivered = (
            _send_discord(discord_webhook_url, title, message, timeout_sec)
            or delivered
        )

    telegram_bot_token = str(config.get("telegram_bot_token") or "").strip()
    telegram_chat_id = str(config.get("telegram_chat_id") or "").strip()
    if telegram_bot_token and telegram_chat_id:
        delivered = (
            _send_telegram(
                telegram_bot_token,
                telegram_chat_id,
                title,
                message,
                timeout_sec,
            )
            or delivered
        )

    if not delivered:
        log.debug("No notification target delivered the alert.")
    return delivered


def _send_discord(
    webhook_url: str,
    title: str,
    message: str,
    timeout_sec: float,
) -> bool:
    payload = json.dumps(
        {"content": f"**{title}**\n{message}"},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            ok = 200 <= response.status < 300
        if ok:
            log.info("Discord notification sent.")
        else:
            log.warning(f"Discord notification returned HTTP {response.status}.")
        return ok
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning(f"Discord notification failed: {exc}")
        return False


def _send_telegram(
    bot_token: str,
    chat_id: str,
    title: str,
    message: str,
    timeout_sec: float,
) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": f"{title}\n{message}",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            ok = 200 <= response.status < 300
        if ok:
            log.info("Telegram notification sent.")
        else:
            log.warning(f"Telegram notification returned HTTP {response.status}.")
        return ok
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning(f"Telegram notification failed: {exc}")
        return False


def _get_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _get_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
