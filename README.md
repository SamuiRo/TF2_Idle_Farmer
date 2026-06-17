# TF2 Idle Farmer

Windows-focused Python automation for cycling saved Steam accounts, launching
Team Fortress 2, idling on configured servers, and recording weekly item drops.

This project intentionally stays at the OS/client-configuration level: Steam
launch flags, TF2 config files, Windows window input, screenshots, process
management, and the public Steam inventory endpoint. It does not read or patch
game memory, inject code, hook the game process, or modify TF2 binaries.

## Requirements

- Windows
- Python 3.10+
- Steam and Team Fortress 2 installed
- Accounts must have been logged in manually at least once with "Remember my
  password" enabled

Install:

```powershell
pip install -e .
```

Python 3.10 only:

```powershell
pip install tomli
```

## Quick Start

### 1. Accounts

Create `config/accounts.txt`:

```text
my_account_1:76561198XXXXXXXXX
my_account_2
```

`login:SteamID64` enables Steam Inventory API drop detection for that account.
`login` without a SteamID64 can still run, but drop detection falls back to
`console.log`, which is less reliable.

### 2. Servers

Create `config/servers.txt`:

```text
103.28.54.100:27015
185.107.96.50:27015
```

### 3. Paths

Edit `config/settings.toml`:

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
# console_log  = "C:/.../Team Fortress 2/tf/console.log"
```

Use forward slashes in paths. If `console_log` is omitted, it is derived from
`tf2_cfg_dir`.

### 4. Steam Inventory API

Recommended for reliable drop detection:

1. Get a Steam Web API key at <https://steamcommunity.com/dev/apikey>
2. Set the account inventory to Public
3. Add the account SteamID64 to `config/accounts.txt`
4. Add the API key to `config/settings.toml`

```toml
[steam_api]
api_key = "YOUR_KEY_HERE"
inventory_poll_attempts = 4
inventory_poll_interval_sec = 15
```

The inventory endpoint is used as a before/after snapshot. Post-session polling
is enabled because Steam inventory updates can lag behind TF2 shutdown.

### 5. Run

Single run:

```powershell
python main.py
```

Scheduled weekly mode:

```powershell
python main.py --schedule
```

## Settings Reference

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
# console_log  = "C:/.../Team Fortress 2/tf/console.log"

[timing]
idle_duration_min          = 65
idle_duration_max          = 80
steam_startup_wait         = 60
steam_warmup_min           = 30
steam_warmup_max           = 60
tf2_startup_wait           = 90
pause_between_accounts_min = 15
pause_between_accounts_max = 35

[behavior]
shuffle_accounts = true
shuffle_servers  = true
mouse_activity   = true

# Item-drop popup dismissal during idle:
# "auto"              = screenshot a small TF2 UI region; click only if it looks like a popup
# "mouse"             = click the configured TF2 client coordinate every check interval
# "off"               = never dismiss drop popups during idle
# "keyboard_fallback" = old Enter/Escape behavior; kept only for manual fallback
drop_popup_dismiss = "auto"
drop_popup_check_interval_sec = 120
drop_popup_click_x = 400
drop_popup_click_y = 520
drop_popup_detect_region = [220, 120, 360, 360]

[connection_check]
enabled = true
timeout_sec = 90
interval_sec = 10
require_success = false
retry_servers_on_failure = true
max_server_attempts = 3
detect_failure_dialog = true
failure_dialog_grace_sec = 30
failure_dialog_matches_required = 2
failure_dialog_detect_region = [180, 140, 440, 300]
save_failure_screenshot = true

[notifications]
enabled = false
# discord_webhook_url = "https://discord.com/api/webhooks/..."
# telegram_bot_token = "123456:ABCDEF..."
# telegram_chat_id = "123456789"
timeout_sec = 10

[steam_api]
api_key = "YOUR_KEY_HERE"
inventory_poll_attempts = 4
inventory_poll_interval_sec = 15
```

## Runtime Flow

For each account, the farmer:

1. Aborts if another known game process is already running.
2. Reuses the current Steam session if it is already logged into the right
   account; otherwise switches `loginusers.vdf` and relaunches Steam.
3. Generates `autoexec.cfg` with performance settings and `connect <server>`.
4. Clears stale `console.log`.
5. Takes a pre-session inventory snapshot when API tracking is configured.
6. Launches TF2 in a small low-resource window.
7. Checks whether TF2 connected or hit a known connection failure.
   Failed server connections can retry alternate servers before skipping the
   account.
8. Dismisses the server MOTD with startup-only keyboard input.
9. Idles for the configured duration.
10. Optionally dismisses item-drop popups through the configured popup mode.
11. Quits TF2, removes generated `autoexec.cfg`, polls the post-session
    inventory, computes new items, and saves `data/drops.json`.
12. Quits Steam and pauses before the next account.

## Drop Detection

Primary detection is Steam Inventory API before/after comparison. Internally
the inventory is treated as item-name counts, so duplicate item changes are
handled correctly.

Fallback detection is `console.log` parsing. It exists for accounts without a
SteamID64/API setup, but it is not considered reliable for modern TF2 drops.

Item-drop popup dismissal is not drop detection. It only clears the visible UI
popup during idle.

## Connection Check

After TF2 starts and the map-load wait finishes, the farmer polls the fresh
`console.log` before starting the idle timer. Explicit connection failures like
`Connection failed`, `Disconnected`, `Server is full`, or kick/ban messages
skip the session for that account/server.

The optional dialog detector screenshots a small centered TF2 client-area
region and looks for a stable gray Source-engine dialog across multiple polls.
It is deliberately conservative so a normal loading screen is less likely to
be treated as a failure.

If `[notifications]` is configured, failed connection checks send a best-effort
Discord webhook and/or Telegram message with the account, server, attempt, and
evidence. When `save_failure_screenshot` is enabled, a TF2 client screenshot is
saved under `logs/connection_failures/` and its path is included in the alert.

`retry_servers_on_failure = true` lets the farmer quit TF2, generate a new
`autoexec.cfg` for another server, and try again up to `max_server_attempts`
before skipping the account.

## Popup Dismissal

The recommended mode is:

```toml
drop_popup_dismiss = "auto"
```

`auto` screenshots a small region of the TF2 client area and posts a mouse
click to the TF2 window at the configured client-area coordinate only if the
region looks like a centered TF2 popup. This avoids moving the real cursor and
also avoids periodic blind Enter presses, which can interact with server votes
or menus.

Use `mouse` only after confirming `drop_popup_click_x` and
`drop_popup_click_y` on your TF2 window. Use `off` to leave item popups alone.
`keyboard_fallback` exists only for manual troubleshooting and is not the
recommended mode.

## Known Bad Approaches

Implementation approaches that should not be retried casually are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#known-bad-approaches). That list is
kept in one place so future changes do not drift.

## autoexec.cfg

The farmer generates `tf/cfg/autoexec.cfg` before a session and deletes it
after TF2 exits. If you already use a personal `autoexec.cfg`, rename it before
running this tool and load it separately for manual play.

If the farmer is interrupted, check and remove the generated file manually:

```powershell
Test-Path "C:/.../Team Fortress 2/tf/cfg/autoexec.cfg"
```

## Logs and Data

- `logs/farmer.log` - rotating runtime log
- `data/drops.json` - persisted drop history

Example record:

```json
{
  "my_account_1": [
    {
      "date": "2026-06-17",
      "timestamp": "2026-06-17T15:44:55",
      "items": ["The Holy Mackerel"],
      "session_duration_min": 71.3
    }
  ]
}
```

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'tomllib'` | Python 3.10 | `pip install tomli` |
| Session aborted because a game is running | Active game guard fired | Close the game and restart the farmer |
| Steam shows account picker | Account was not saved | Log in manually once with "Remember my password" |
| TF2 startup timeout | Slow disk or first launch after update | Increase `tf2_startup_wait` |
| MOTD not dismissed | Server has extra welcome screens | Increase MOTD constants in `modules/constants.py` |
| Drop popup click misses | Coordinate differs for your UI/window | Tune `drop_popup_click_x` and `drop_popup_click_y` |
| `auto` mode never clicks | Detector is too conservative | Tune `drop_popup_detect_region` or test `mouse` mode |
| No drops recorded | API not configured or inventory private | Add API key, SteamID64, and set inventory Public |
| Inventory diff appears late | Steam inventory update lag | Increase `inventory_poll_attempts` or interval |
| TF2 auto-connects during manual play | Leftover generated `autoexec.cfg` | Delete `tf/cfg/autoexec.cfg` |

## More Details

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for implementation details and
[docs/Resources.md](docs/Resources.md) for idle-server discovery links.
