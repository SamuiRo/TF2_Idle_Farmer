# TF2 Idle Farmer

Automated weekly drop-farming tool for Team Fortress 2. Cycles through multiple Steam accounts, launches TF2 in minimal-resource mode, idles on a configured server for 65–80 minutes, logs results, and repeats next week.

---

## Requirements

- **Python 3.11+** recommended (`tomllib` is in the standard library)
- **Python 3.10** — install the `tomli` backport (see below)
- **Windows** (Steam and TF2 are Windows-only)
- All accounts must have been logged in manually at least once with **"Remember my password"** checked

### Install dependencies

```
pip install -e .
```

**Python 3.10 only:**
```
pip install tomli
```

---

## Quick start

### 1. Configure accounts

Edit `config/accounts.txt` — one account per line:

```
my_account_1:76561198XXXXXXXXX
my_account_2
```

`login:SteamID64` enables drop detection via the Steam Inventory API (recommended).
`login` alone falls back to `console.log` parsing.

Every account must have been logged in manually at least once with **"Remember my password"** checked.

### 2. Configure servers

Edit `config/servers.txt` — one `IP:PORT` per line:

```
103.28.54.100:27015
185.107.96.50:27015
```

### 3. Configure paths

Edit `config/settings.toml`. Use forward slashes — they work on Windows:

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
```

### 4. (Optional) Enable Steam Inventory API drop tracking

TF2 does not write item drops to `console.log`. The inventory API detects drops by comparing your inventory before and after each session.

1. Get a free Steam Web API key at <https://steamcommunity.com/dev/apikey>
2. Set your Steam inventory to **Public** (Edit Profile → Privacy Settings)
3. Find your SteamID64 at <https://steamidfinder.com>
4. Add to `config/settings.toml`:

```toml
[steam_api]
api_key = "YOUR_KEY_HERE"
```

5. Add Steam IDs to `config/accounts.txt`:

```
my_account_1:76561198XXXXXXXXX
```

Accounts without a Steam ID silently fall back to `console.log` parsing — no session is skipped.

### 5. Run

**Single run (all accounts once):**
```
python main.py
```

**Scheduled mode (runs every Monday at 09:00, immediately on first start):**
```
python main.py --schedule
```

---

## How it works

Before starting each account's session, the farmer performs two safety checks:

- **Active game check** — if any game process (TF2, CS2, Dota 2, etc.) is running, the session is aborted to avoid interrupting active play. Close the game and restart the farmer.
- **Active account check** — if Steam is already running as the correct account, the farmer skips shutdown and re-login entirely, saving time. If Steam is running as a different account, it restarts Steam with the right one.

For each account:

1. Skip Steam restart if already logged in as the correct account; otherwise switch account in `loginusers.vdf` and relaunch Steam
2. Pick a server from `servers.txt`, generate `autoexec.cfg`, and clear `console.log`
3. Take a pre-session inventory snapshot (if API is configured)
4. Launch TF2 with minimal-resource flags (`-novid -nosound -sw -low -condebug …`)
5. Wait 20–40 s for the map to load, dismiss the server MOTD (8 attempts × 4 s)
6. Idle 65–80 min; a background thread tails `console.log` live; occasional random mouse moves / key presses every 3–10 min
7. Quit TF2 and delete `autoexec.cfg`
8. Take a post-session inventory snapshot and diff — the difference is the drops
9. Save results to `data/drops.json`; delete `console.log`
10. Quit Steam and pause before the next account

Drop detection priority: inventory API diff → `console.log` watcher fallback.

> **MOTD:** The server welcome popup must be dismissed for the drop timer to start. Item-drop popups dismiss themselves and do not affect the timer.

---

## autoexec.cfg

The farmer generates `autoexec.cfg` before each session (performance caps + `connect <server>`) and **deletes it after TF2 quits** — including on crash via emergency cleanup — so normal play is unaffected.

**If you had your own autoexec.cfg**, rename it before running the farmer:

```
tf/cfg/autoexec_mine.cfg   ← safe here; exec it from config.cfg if needed
```

**If the farmer was interrupted** before cleanup ran, delete `autoexec.cfg` manually:

```
C:\Program Files (x86)\Steam\steamapps\common\Team Fortress 2\tf\cfg\autoexec.cfg
```

Check whether it exists:
```powershell
Test-Path "C:\...\Team Fortress 2\tf\cfg\autoexec.cfg"
```

---

## Settings reference (`settings.toml`)

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
# console_log  = "C:/.../Team Fortress 2/tf/console.log"   # derived from tf2_cfg_dir if omitted

[timing]
idle_duration_min          = 65   # min idle time per account (minutes)
idle_duration_max          = 80   # max idle time per account (minutes)
steam_startup_wait         = 60   # timeout waiting for Steam process (seconds)
steam_warmup_min           = 30   # min extra wait after Steam detected (seconds)
steam_warmup_max           = 60   # max extra wait after Steam detected (seconds)
tf2_startup_wait           = 90   # timeout waiting for TF2 process (seconds)
pause_between_accounts_min = 15   # min pause between accounts (seconds)
pause_between_accounts_max = 35   # max pause between accounts (seconds)

[behavior]
shuffle_accounts = true   # randomise account order each run
shuffle_servers  = true   # pick a random server each session
mouse_activity   = true   # move mouse during idle

[steam_api]
api_key = "YOUR_KEY_HERE"   # omit to disable inventory tracking
```

**Slow machines / HDD** — if TF2 or Steam takes longer to start:

```toml
steam_warmup_min = 45
steam_warmup_max = 90
tf2_startup_wait = 150
```

For lower-level tuning (MOTD attempts, mouse parameters, idle action probability), edit `modules/constants.py` directly — every constant is documented.

---

## Logs & data

- **`logs/farmer.log`** — full run log, rotates at 5 MB (3 backups kept)
- **`data/drops.json`** — cumulative drop history per account

```json
{
  "my_account_1": [
    {
      "date": "2025-04-20",
      "timestamp": "2025-04-20T15:44:55",
      "items": ["The Holy Mackerel"],
      "session_duration_min": 71.3
    }
  ]
}
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: No module named 'tomllib'` | Python 3.10 | `pip install tomli` |
| Session aborted — "game process running" | A game is open | Close it, then restart the farmer |
| Steam shows account-picker GUI | No saved password | Log in manually once with "Remember my password" |
| `[WinError 123] filename syntax incorrect` | Bad path in `settings.toml` | Fix the path; use forward slashes |
| TF2 startup timeout | Slow HDD / first launch after update | Increase `tf2_startup_wait` to `150` or more |
| MOTD not dismissed | Server has extra welcome screens | Increase `MOTD_DISMISS_ATTEMPTS` in `modules/constants.py` |
| No drops recorded after session | `console.log` not written | Verify `-condebug` is in launch options and `tf2_cfg_dir` is correct |
| No drops despite items in inventory | Inventory API not configured | Add `[steam_api]` key + Steam ID to `accounts.txt`; set inventory to Public |
| `Inventory is private` warning | Profile privacy not Public | Steam profile → Edit Profile → Privacy Settings → Inventory: Public |
| TF2 connects to a server automatically | Leftover `autoexec.cfg` | Delete `tf/cfg/autoexec.cfg` manually |