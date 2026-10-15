#!/usr/bin/env python3
"""
Adaptive sampling flags for a Campbell Scientific logger
--------------------------------------------------------
Run every 15 minutes from cron. Each run:

  1. Checks United Utilities' storm overflow feed for the Cunsey Beck catchment.
     OverflowFlag = 1 while an overflow is discharging, and for OVERFLOW_HOLD_H
     hours after the most recent one stopped.
  2. Checks the Open-Meteo forecast.
     RainFlag = 1 if at least RAIN_THRESHOLD_MM is forecast in the next
     RAIN_WINDOW_H hours.
  3. Increases Heartbeat by one, so the logger knows this script is alive.
  4. Writes all three to the logger's Public table with the web API
     (SetValueEx). They are sent on every run, so a logger restart that resets
     its Public variables is corrected within 15 minutes.

If one data source fails, its last good value is reused for up to
MAX_STALE_H hours and then treated as 0.

Usage:
    python logger_flags.py --dry-run   # show the decision, don't touch the logger
    python logger_flags.py             # normal run
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------- settings --

# Logger web server and a .csipasswd user with *control* access.
# Set these as environment variables rather than in the file if you can.
LOGGER_URL = os.environ.get("LOGGER_URL", "http://192.168.1.50")
LOGGER_USER = os.environ.get("LOGGER_USER", "user")
LOGGER_PASS = os.environ.get("LOGGER_PASS", "pass")

# Names of the Public variables in the CRBasic program
VAR_RAIN = "RainFlag"
VAR_OVERFLOW = "OverflowFlag"
VAR_HEARTBEAT = "Heartbeat"

RAIN_THRESHOLD_MM = 5.0   # total rain in the window that switches RainFlag on
RAIN_WINDOW_H = 24        # how far ahead to look
OVERFLOW_HOLD_H = 24      # keep OverflowFlag on this long after a spill stops
MAX_STALE_H = 6           # how long to trust a last good value if a source fails

# Optional phone notification when a flag changes (same ntfy app as before).
# Leave empty to switch off.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

OVERFLOW_LAYER = (
    "https://services5.arcgis.com/5eoLvR0f8HKb7HWP/arcgis/rest/services/"
    "United_Utilities_Storm_Overflow_Activity/FeatureServer/0"
)
BBOX = "-3.03,54.32,-2.93,54.40"
WATERCOURSES = {"cunsey beck", "black beck", "lake windermere"}

RAIN_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=54.35&longitude=-2.983"
    "&hourly=rain&models=ecmwf_ifs&forecast_days=10"
)

STATE_FILE = Path(__file__).with_name("logger_flags_state.json")
TIMEOUT = 30

# ------------------------------------------------------------------ helpers --


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def ms_to_utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms else None


# ---------------------------------------------------------- data sources --


def check_overflows(now: datetime) -> tuple[bool, str]:
    params = {
        "where": "1=1",
        "geometry": BBOX,
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "Id,Status,StatusStart,LatestEventEnd,ReceivingWaterCourse",
        "returnGeometry": "false",
        "f": "json",
    }
    r = requests.get(f"{OVERFLOW_LAYER}/query", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])

    sites = [
        f["attributes"] for f in data.get("features", [])
        if str(f["attributes"].get("ReceivingWaterCourse") or "").strip().lower() in WATERCOURSES
    ]
    if not sites:
        raise RuntimeError("no overflows found in the catchment")

    active = [s["Id"] for s in sites if s.get("Status") == 1]
    if active:
        return True, f"discharging now: {', '.join(active)}"

    # Most recent stop time. LatestEventEnd is sometimes empty in UU's feed,
    # in which case StatusStart of a stopped overflow is the stop time.
    stops = [
        ms_to_utc(s.get("LatestEventEnd") or s.get("StatusStart"))
        for s in sites if s.get("Status") == 0
    ]
    stops = [t for t in stops if t]
    if stops:
        last_stop = max(stops)
        if now - last_stop < timedelta(hours=OVERFLOW_HOLD_H):
            hrs = (now - last_stop).total_seconds() / 3600
            return True, f"holding: last spill stopped {hrs:.1f} h ago"
    return False, "no recent discharges"


def check_rain(now: datetime) -> tuple[bool, str]:
    r = requests.get(RAIN_URL, timeout=TIMEOUT)
    r.raise_for_status()
    hourly = r.json()["hourly"]
    end = now + timedelta(hours=RAIN_WINDOW_H)
    this_hour = now.replace(minute=0, second=0, microsecond=0)
    total = 0.0
    for t, mm in zip(hourly["time"], hourly["rain"]):
        # Open-Meteo returns GMT times without a zone when no timezone is requested
        ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        if this_hour <= ts < end and mm is not None:
            total += mm
    on = total >= RAIN_THRESHOLD_MM
    return on, f"{total:.1f} mm forecast in next {RAIN_WINDOW_H} h (threshold {RAIN_THRESHOLD_MM} mm)"


def resolve(name: str, check, now: datetime, state: dict) -> bool:
    """Run a check; on failure fall back to the last good value while it's fresh."""
    try:
        on, why = check(now)
        state[name] = {"value": on, "at": now.isoformat()}
        log(f"{name}: {'ON ' if on else 'off'} ({why})")
        return on
    except Exception as e:  # noqa: BLE001
        prev = state.get(name)
        if prev:
            age = now - datetime.fromisoformat(prev["at"])
            if age < timedelta(hours=MAX_STALE_H):
                log(f"{name}: check failed ({e}); reusing last value from {age.total_seconds()/3600:.1f} h ago")
                return prev["value"]
        log(f"{name}: check failed ({e}) and no recent value; sending 0")
        return False


# ---------------------------------------------------------------- logger --


def set_value(var: str, value: int) -> bool:
    params = {
        "command": "SetValueEx",
        "uri": f"dl:Public.{var}",
        "value": value,
        "format": "json",
    }
    r = requests.get(
        LOGGER_URL.rstrip("/") + "/",
        params=params,
        auth=(LOGGER_USER, LOGGER_PASS),
        timeout=TIMEOUT,
    )
    if r.status_code == 401:
        raise RuntimeError("logger refused the login - check user has control access")
    r.raise_for_status()
    try:
        outcome = r.json().get("outcome")
    except ValueError:
        outcome = None
    if outcome != 1:
        log(f"  {var}: unexpected reply from logger: {r.text[:200]}")
        return False
    return True


def notify(title: str, message: str) -> None:
    if not NTFY_TOPIC:
        return
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(),
                      headers={"Title": title, "Tags": "satellite"}, timeout=TIMEOUT)
    except requests.RequestException:
        pass


# ------------------------------------------------------------------ main --


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't write to the logger")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    state = load_state()

    rain = resolve("rain", check_rain, now, state)
    overflow = resolve("overflow", check_overflows, now, state)
    heartbeat = (state.get("heartbeat", 0) + 1) % 2_000_000_000

    values = {VAR_RAIN: int(rain), VAR_OVERFLOW: int(overflow), VAR_HEARTBEAT: heartbeat}

    if args.dry_run:
        log(f"dry run, would send: {values}")
        return 0

    ok = True
    try:
        for var, val in values.items():
            ok &= set_value(var, val)
    except requests.RequestException as e:
        log(f"couldn't reach the logger at {LOGGER_URL}: {e}")
        ok = False
    except RuntimeError as e:
        log(str(e))
        ok = False

    if ok:
        log(f"logger updated: {values}")
        state["heartbeat"] = heartbeat
        sent = state.get("sent", {})
        for flag, var in (("Rain", VAR_RAIN), ("Overflow", VAR_OVERFLOW)):
            if var in sent and sent[var] != values[var]:
                notify(f"{flag} sampling {'ON' if values[var] else 'off'}",
                       f"{var} set to {values[var]} on the logger")
        state["sent"] = {VAR_RAIN: values[VAR_RAIN], VAR_OVERFLOW: values[VAR_OVERFLOW]}

    STATE_FILE.write_text(json.dumps(state, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
