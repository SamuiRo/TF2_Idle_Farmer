# TF2 Idle Farmer

Automated weekly drop-farming tool for Team Fortress 2.  
Cycles through multiple Steam accounts, launches TF2 in a minimal-resource mode,
idles on a dedicated server for 65–80 minutes (enough to trigger the weekly drop
timer), logs results, and repeats next week.

---

## Requirements

- **Python 3.11+** recommended — `tomllib` is included in the standard library
- **Python 3.10** also works — install the `tomli` backport (see below)
- **Windows** (Steam / TF2 are Windows-only)
- All accounts must already be logged in to Steam at least once on this machine
  (their entries must exist in `loginusers.vdf`)

### Install dependencies

```
pip install -e .
```

**If you are on Python 3.10**, also run:

```
pip install tomli
```

`tomllib` was added to the Python standard library in 3.11. On 3.10 the code
falls back to the third-party `tomli` package which is API-compatible.

---

## Quick start

### 1. Configure accounts

Edit `config/accounts.txt` — one account per line. Two formats are supported and
can be mixed freely:

```
my_account_1:76561198XXXXXXXXX
my_account_2
```

`login:SteamID64` enables drop detection via the Steam Inventory API (recommended).  
`login` alone falls back to `console.log` parsing.

> Every account must have been logged in manually at least once on this machine
> with **"Remember my password"** checked so Steam can sign in automatically.

### 2. Configure servers

Edit `config/servers.txt` — idle-friendly TF2 servers (one `IP:PORT` per line):

```
103.28.54.100:27015
185.107.96.50:27015
```

### 3. Configure paths

Open `config/settings.toml` and set the paths for your Steam installation.
Use forward slashes `/` — they work on Windows and avoid escape issues.

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
```

> If Steam or TF2 is installed on a different drive, adjust accordingly.
> Example for a secondary library on S:\:
> ```toml
> tf2_cfg_dir = "S:/SteamLibrary/steamapps/common/Team Fortress 2/tf/cfg"
> ```

### 4. (Optional) Enable Steam Inventory API drop tracking

TF2 does not write item drops to `console.log` — they appear only as in-game
chat messages that `-condebug` does not capture. The inventory API detects drops
by comparing your inventory before and after each session.

**Setup:**

1. Get a free Steam Web API key at <https://steamcommunity.com/dev/apikey>
2. Set your Steam profile inventory to **Public** (Steam profile → Edit Profile → Privacy Settings)
3. Find your Steam ID (64-bit) at <https://steamidfinder.com>
4. Add to `config/settings.toml`:

```toml
[steam_api]
api_key = "YOUR_KEY_HERE"
```

5. Add Steam IDs to `config/accounts.txt`:

```
my_account_1:76561198XXXXXXXXX
my_account_2:76561198XXXXXXXXX
```

If the API key is missing or a Steam ID is not set for an account, that account
silently falls back to `console.log` parsing — no session is skipped.
If an inventory is private, a warning is logged and the fallback applies.

### 5. Run

**Single run (all accounts once):**
```
python main.py
```

**Scheduled mode (repeats every Monday at 09:00, runs immediately on first start):**
```
python main.py --schedule
```

---

### What goes where

`config/settings.toml` is for values **you** control: file paths, how long to
idle, how long to wait for Steam to start. You are expected to edit this file.

`modules/constants.py` is for values the **code** controls: Steam launch flags,
TF2 process names, key sequences, Bézier curve parameters, log rotation limits.
You should only touch this file if you are modifying the automation logic itself.

---

## How it works

For each account in `accounts.txt`:

1. Shut down any running Steam instance
2. Edit `loginusers.vdf` — set this account as `MostRecent` and `RememberPassword = 1`
3. Launch Steam with `-login <username> -silent` so it signs in without showing the account-picker GUI
4. Wait for Steam to fully initialise and complete login (~15 s stabilisation after process detection)
5. Pick a random server from `servers.txt` and generate `autoexec.cfg` with performance tweaks and `connect <server>`
6. Clear `console.log` so stale drops from previous sessions are not re-counted
7. **Take a pre-session inventory snapshot** (if API key and Steam ID are configured)
8. Launch TF2 with minimal-resource flags (`-novid -nosound -sw -low -condebug …`)
9. Wait 20–40 s for the map to load, then automatically dismiss the server MOTD window (8 attempts × 4 s — covers servers with multiple stacked welcome screens)
10. Idle 65–80 min with a live background watcher tailing `console.log` for drops; occasional random mouse moves / key presses every 3–10 min
11. Quit TF2 and delete `autoexec.cfg`
12. **Take a post-session inventory snapshot** and diff against the pre-session snapshot — the difference is the drops
13. Save results to `data/drops.json` → move to next account

Drop detection priority: inventory API diff → console.log watcher fallback.

> **Note on the MOTD screen:** The welcome popup that appears after connecting
> *must* be dismissed for the drop timer to start. The death-notification popup
> that appears when an item drops does not need to be closed — it disappears on
> its own and does not affect the timer.

---

## autoexec.cfg — how it works and what the farmer does with it

### What the farmer writes

Before each idle session the farmer generates `autoexec.cfg` in your TF2 cfg
directory. The file contains two things:

- **Performance caps** (`fps_max 20`, `mat_picmip 4`, `-nosound`, etc.) that
  reduce CPU/GPU load to a minimum while idling.
- **`connect <server>`** — the command that makes TF2 join the idle server
  automatically on startup.

### Why the file is deleted after each session

If `autoexec.cfg` were left in place after the farmer finishes, every
subsequent manual launch of TF2 would run at capped 20 FPS with degraded
graphics and no sound, and immediately try to connect to whatever idle server
was last used.

The farmer **deletes `autoexec.cfg` as part of cleanup** immediately after TF2
is killed. This happens whether the session completes normally, is skipped due
to a startup timeout, or crashes mid-way — the emergency cleanup path also
removes the file.

### If you had an autoexec.cfg before using the farmer

The farmer **overwrites** (not merges) `autoexec.cfg` at the start of each
session. If you had your own `autoexec.cfg` with personal settings, it will be
lost when the farmer runs.

**To preserve your personal autoexec**, rename it before running the farmer:

```
tf/cfg/autoexec.cfg      ← farmer will overwrite this
tf/cfg/autoexec_mine.cfg ← your personal settings, safe here
```

Then load your personal file from within TF2's console manually, or add an
`exec autoexec_mine` line to a different cfg that TF2 loads on its own (such
as `tf/cfg/config.cfg`).

### Manually deleting a leftover autoexec.cfg

If the farmer was interrupted (e.g. you force-quit Python) before cleanup
ran, `autoexec.cfg` may still exist. Delete it manually before your next
normal play session:

1. Open File Explorer and navigate to your TF2 cfg folder:
   ```
   C:\Program Files (x86)\Steam\steamapps\common\Team Fortress 2\tf\cfg\
   ```
   (adjust the drive letter if Steam is installed elsewhere)
2. Delete `autoexec.cfg`.
3. Launch TF2 normally — it will start with default settings.

TF2 does not require `autoexec.cfg` to run. Deleting it is completely safe.

### Checking whether a leftover file exists

Open PowerShell or Command Prompt and run:

```powershell
Test-Path "C:\Program Files (x86)\Steam\steamapps\common\Team Fortress 2\tf\cfg\autoexec.cfg"
```

`True` means the file exists and should be deleted before playing manually.
`False` means the folder is already clean.

---

## Settings reference (`settings.toml`)

```toml
[paths]
steam_exe      = "C:/Program Files (x86)/Steam/steam.exe"
loginusers_vdf = "C:/Program Files (x86)/Steam/config/loginusers.vdf"
tf2_cfg_dir    = "C:/Program Files (x86)/Steam/steamapps/common/Team Fortress 2/tf/cfg"
# Optional: override the console.log path (defaults to tf2_cfg_dir/../console.log)
# console_log  = "C:/.../Team Fortress 2/tf/console.log"

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
api_key = "YOUR_KEY_HERE"   # optional — omit to disable inventory tracking
```

### Tuning for slow machines / HDD

If TF2 takes longer than 90 s to start, or Steam does not finish logging in
before TF2 launches, increase these values:

```toml
steam_warmup_min   = 45
steam_warmup_max   = 90
tf2_startup_wait   = 150
```

---

## Tuning automation behaviour

Values in `settings.toml` cover the most common adjustments. For lower-level
tuning — MOTD dismiss attempts, mouse movement parameters, idle action
probability, drop-popup dismiss interval — edit `modules/constants.py` directly.
Every constant is documented with its units and effect.

---

## Logs & data

- **`logs/farmer.log`** — full run log with timestamps, rotates at 5 MB (3 backups kept)
- **`data/drops.json`** — cumulative drop history per account

Example `drops.json` entry:

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
| `ModuleNotFoundError: No module named 'tomllib'` | Python 3.10 or older | `pip install tomli` |
| Steam shows account-picker GUI | Account has no saved password | Log in manually once with "Remember my password" checked |
| `[WinError 123] filename syntax incorrect` | Wrong path in `settings.toml` (e.g. `CS:\` instead of `C:\`) | Fix the path; use forward slashes `/` |
| TF2 startup timeout | Slow HDD / first launch after update | Increase `tf2_startup_wait` to `150` or more |
| MOTD not dismissed / drop timer not starting | Server has extra welcome screens | Increase `MOTD_DISMISS_ATTEMPTS` in `modules/constants.py` |
| No drops recorded after session | `console.log` not written | Verify `-condebug` is in launch options and `tf2_cfg_dir` path is correct |
| No drops recorded despite items in inventory | Inventory API not configured | Add `[steam_api]` key to `settings.toml` and Steam ID to `accounts.txt`; set inventory to Public |
| `Inventory is private` warning in logs | Steam profile privacy set to Friends-only or Private | Go to Steam profile → Edit Profile → Privacy Settings → set Inventory to Public |
| TF2 launches at 1 FPS or connects to a server automatically after farming | Leftover `autoexec.cfg` from an interrupted session | Delete `autoexec.cfg` from your `tf/cfg/` folder manually (see [autoexec.cfg section](#autoexeccfg--how-it-works-and-what-the-farmer-does-with-it) above) |