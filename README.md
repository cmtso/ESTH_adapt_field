# Adaptive sampling – Esthwaite

This setup changes how often the **SUNA nitrate sensor** is read, based on conditions at the site and on outside information (rain forecasts and storm overflow activity). The thermistor string is always read every 15 min.

There are two parts:

| File | Runs on | What it does |
|---|---|---|
| `adaptive_sampling.cr1x` | Campbell CR1000X / CR6 logger | Takes all the readings, works out Rules 1–4 itself, and stores the data tables. |
| `logger_flags.py` | A PC or server with internet access that can reach the logger | Every 15 min, checks the rain forecast and storm overflows, then sets `RainFlag`, `OverflowFlag` and `Heartbeat` on the logger. |

The logger works without the PC. If the PC stops, Rules 1–4 carry on, and the rain and overflow sampling switches itself off after 6 h.

- `logger_flags_state.json` is an example output file of `logger_flags.py`, which stores the latest value of `RainFlag`, `OverflowFlag` and `Heartbeat`.
- `cunsey_beck_app.py` is a very simple streamlit app demonstrating the retreival of live rainfall prediction and UU storm overflow data using application programming interfaces (API) calls. 


*Disclaimer: this project uses AI-assisted development tools. Human review all output. The authors are solely responsible for the content and fitness-of-purpose of this project.*

*Claude Code (Anthropic) `claude-opus-4-8`* Agentic mode was not used.

*Currenlty, the logger programme (*.cr1x) is not ready for deployment.*
---

## 1. Sampling rules

### Routine sampling (always on)

| What | Interval | Table |
|---|---|---|
| Thermistors, `Temp1m`, `Temp4m`, battery, wind, `SPFD` | 15 min | `Normal` |
| SUNA | `SUNABaseMins`, default 60 min | `SUNA` |

### Extra SUNA readings

When more than one rule is active, the SUNA is read at the fastest interval any of them asks for. Every SUNA reading is saved in the `SUNA` table, together with the flags showing which rule asked for it.

| Rule | Set by | Condition | SUNA reading | Rule table |
|---|---|---|---|---|
| **Rain** | `logger_flags.py` | ≥ 5 mm of rain forecast in the next 24 h (ECMWF model available at Open-Meteo API) | every **5 min** while on | `RainFast` |
| **Overflow** | `logger_flags.py` | A United Utilities storm overflow on Cunsey Beck / Black Beck / Windermere Shore is discharging, or has stopped within the last 24 h | every **1 min** while on | `OverflowFast` |
| **1 – Sudden change** | logger | The change in `SUNA_Nitrate_mgL` since the previous reading is more than **3 × the SD of all nitrate readings up to the previous one** (needs 30 readings first) | every **10 min** for **3 h**; each new big change restarts the 3 h | `Rule1Fast` |
| **2 – Rising nitrate** | logger, checked hourly | Mean nitrate over the last 0–24 h > mean over 24–48 h ago (based on hourly means) | every **30 min** while true | `Rule2Fast` |
| **3 – Warm / bright** | logger, checked every 15 min | `Temp4m` > 18 °C **OR** `SPFD` > 800 | **one** reading, at most once every **6 h** | `Rule3Event` |
| **4 – Wind mixing** | logger, checked hourly | 6 h mean `WindSpd` > 2.5 m/s **AND** `Temp1m` has dropped ≥ 0.2 °C over 6 h (this hour's mean compared with the hour 6 h earlier) | **one** reading, at most once every **6 h** | `Rule4Event` |

Details to know when reading the data:

- **Restarts:**
  - Rule 1's SD starts again from zero when the program restarts, so the rule is off for its first 30 SUNA readings.
  - Rule 2 needs at least half of each window to have data, so it stays off for about 36 h after a restart.
  - Rule 4 needs 7 h of data.
- **Extra readings feed the rules:** SUNA readings triggered by rain, overflow or Rules 3/4 also count towards Rule 1's SD and Rule 2's hourly means.
- **Watchdog:** if `Heartbeat` hasn't changed for 6 h (`TimeoutSecs`), the logger turns off `RainFlag` and `OverflowFlag`. Rules 1–4 are not affected.
- **Settings:** all thresholds and intervals are `Const` lines near the top of `adaptive_sampling.cr1x`.

---

## 2. Before deployment: finish the logger program

Search `adaptive_sampling.cr1x` for **`<<< CHECK`**. Every one of these lines must be dealt with before the program goes to the field:

1. **Sensor code:**
   - Add the real measurement instructions for the thermistor string, `Temp1m`, `Temp4m`, `WindSpd` and `SPFD`.
   - Add the SUNA code in `Sub MeasureSUNA`: power switching, warm-up and reading.
   - The lines in the file are commented-out examples only.
2. **SUNA variables:** `SUNA_Nitrate_uM`, `SUNA_Abs254`, `SUNA_Abs350`, `SUNA_FitRMSE` and `SUNA_DarkMean` are placeholders. Rename them or remove them to match the SUNA output.
3. **SUNA timing:**
   - With `OverflowFlag` on, the SUNA is read every minute, so a full reading must take less than 1 min.
   - If warm-up plus reading takes longer than the 10 s scan, move `MeasureSUNA` into a `SlowSequence`.
4. **`SPFD` units:** the 800 threshold assumes W m⁻². If `SPFD` is in µmol m⁻² s⁻¹, change `R3SolarMax` (800 W m⁻² is roughly 1600–1800 µmol m⁻² s⁻¹).
5. **Duplicate declarations:** if this code is merged into an existing program, declare each variable only once.
6. **Optional actions:** Rules 3 and 4 have a marked place for extra actions, e.g. pulsing an autosampler.
7. **Compile:** compile in CRBasic Editor, fix any errors, then send the program to the logger with LoggerNet / PC400 / Device Configuration Utility.

---

## 3. Logger set-up for remote flags

`logger_flags.py` uses the logger's built-in web server (HTTP API, `SetValueEx`). On the logger:

1. **Network:** connect the logger to a network the PC can reach, via Ethernet or a cellular modem, and give it a fixed IP address or hostname.
2. **Web server:** in Device Configuration Utility, make sure HTTP is enabled (Settings → Network Services).
3. **User account:** create a `.csipasswd` user with **read/write** access, so it can set values as well as read them. Use a strong password: anyone with these details can change the sampling.
4. **Test from the PC:** a browser request to
   `http://<logger-ip>/?command=SetValueEx&uri=dl:Public.Heartbeat&value=1&format=json`
   should return `"outcome":1`.

---

## 4. PC / server set-up for `logger_flags.py`

Requirements: Python 3.9+ and the `requests` package (`pip install requests`).

1. Copy `logger_flags.py` to the machine. Its state file, `logger_flags_state.json`, is created next to it.

2. Set the connection details as environment variables (don't put them in the file):

   ```bash
   export LOGGER_URL="http://<logger-ip>"
   export LOGGER_USER="<csipasswd user>"
   export LOGGER_PASS="<password>"
   export NTFY_TOPIC="<optional ntfy.sh topic for phone alerts>"
   ```

3. Test without touching the logger:

   ```bash
   python3 logger_flags.py --dry-run
   ```

   It should print the rain and overflow decisions and `dry run, would send: {...}`.

4. Run it once for real and check that `RainFlag`, `OverflowFlag` and `Heartbeat` change in the logger's Public table:

   ```bash
   python3 logger_flags.py
   ```

5. Schedule it every 15 min with cron (`crontab -e`). Cron doesn't see your shell variables, so put the environment variables in the cron line or in a wrapper script:

   ```cron
   */15 * * * * LOGGER_URL=http://<logger-ip> LOGGER_USER=<user> LOGGER_PASS=<pass> /usr/bin/python3 /path/to/logger_flags.py >> /path/to/logger_flags.log 2>&1
   ```

The script's settings are at the top of `logger_flags.py`: `RAIN_THRESHOLD_MM`, `RAIN_WINDOW_H`, `OVERFLOW_HOLD_H` and `MAX_STALE_H`. If a data source fails, the script reuses its last good value for up to 6 h, then sends 0.

---

## 5. Checks after deployment

| Check | Where | Expected |
|---|---|---|
| Heartbeat is updating | Logger Public table: `SecsSinceHB` | Resets to 0 about every 15 min. If it climbs past 21600, the PC isn't reaching the logger. |
| Flags arrive | Public table: `RainFlag`, `OverflowFlag` | Match the latest line in `logger_flags.log` |
| Routine SUNA | `SUNA` table | One record per `SUNABaseMins` when no rule is on |
| Rules working | `R1Flag`, `R2Flag` in `Normal`; `R1SD`, `R2MeanRecent`, `R2MeanOlder`, `Wind6h`, `T1Drop6h` in Public | Values look sensible (Rule 2 values appear after about 36 h) |
| Logger power | `Normal` table: `BattV` | Watch it, especially during long overflow periods, because 1-min SUNA readings draw a lot of power |

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `logger refused the login` in the log | The `.csipasswd` user doesn't have read/write access, or the password is wrong |
| `couldn't reach the logger` | Network, modem or IP address problem, or HTTP is disabled on the logger |
| Flags switch on and then straight back off | The heartbeat isn't advancing (`SecsSinceHB` > 21600), so the watchdog clears them every scan. Check that all three values are being sent without errors. |
| `check failed … reusing last value` | Open-Meteo or the United Utilities feed is temporarily down. It recovers on its own. |
| Rule 1 never fires | It needs 30 SUNA readings after a restart. Also check `SUNA_Nitrate_mgL` is being filled in by `MeasureSUNA`. |
| Logger skips scans | The SUNA reading is too slow for the 10 s scan. Move `MeasureSUNA` into a `SlowSequence`. |
