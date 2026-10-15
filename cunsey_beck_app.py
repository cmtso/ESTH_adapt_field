"""
Cunsey Beck storm overflows - Streamlit app
-------------------------------------------
Shows the live status of United Utilities storm overflows that discharge into
Cunsey Beck (and, optionally, straight into Windermere), using the near
real-time feed published through the Stream / National Storm Overflow Hub.

Run:
    pip install streamlit requests pandas
    streamlit run cunsey_beck_app.py
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------- settings --

LAYER_URL = (
    "https://services5.arcgis.com/5eoLvR0f8HKb7HWP/arcgis/rest/services/"
    "United_Utilities_Storm_Overflow_Activity/FeatureServer/0"
)

# Box around Hawkshead, Esthwaite Water, Near Sawrey and Cunsey Beck (WGS84)
BBOX = {"xmin": -3.03, "ymin": 54.32, "xmax": -2.93, "ymax": 54.40}

CATCHMENT = {"cunsey beck", "black beck"}  # Black Beck = Hawkshead -> Esthwaite
WINDERMERE = {"lake windermere"}

# The feed has no site names, so these are matched by location.
SITE_NAMES = {
    "UUP01370": "Near Sawrey WwTW",
    "UUP00919": "Hawkshead PS",
    "UUP00804": "Windermere shore overflow",
}

STATUS = {
    1: ("Discharging now", "#c0392b", [192, 57, 43]),
    0: ("Not discharging", "#2e7d5b", [46, 125, 91]),
    -1: ("Monitor offline", "#7f8c8d", [127, 140, 141]),
}

UK = ZoneInfo("Europe/London")

RAIN_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=54.35&longitude=-2.983"
    "&hourly=rain&models=ecmwf_ifs&forecast_days=10"
)
WET_HOUR_MM = 0.1   # an hour counts as rainy at or above this
HEAVY_24H_MM = 10   # 24-hour total treated as heavy rain

# ------------------------------------------------------------------- data --


@st.cache_data(ttl=600, show_spinner="Fetching the latest data from United Utilities…")
def fetch_overflows() -> tuple[list[dict], datetime]:
    """Query the feed. Cached for 10 minutes unless Refresh is pressed."""
    params = {
        "where": "1=1",
        "geometry": f"{BBOX['xmin']},{BBOX['ymin']},{BBOX['xmax']},{BBOX['ymax']}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    r = requests.get(f"{LAYER_URL}/query", params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", str(data["error"])))
    rows = [f["attributes"] for f in data.get("features", [])]
    return rows, datetime.now(timezone.utc)


@st.cache_data(ttl=1800, show_spinner="Fetching the rain forecast…")
def fetch_rain() -> pd.DataFrame:
    """Hourly rain forecast (mm) from Open-Meteo. Times come back in GMT."""
    r = requests.get(RAIN_URL, timeout=30)
    r.raise_for_status()
    hourly = r.json()["hourly"]
    df = pd.DataFrame({"time": pd.to_datetime(hourly["time"]), "rain": hourly["rain"]})
    df["time"] = df["time"].dt.tz_localize("UTC").dt.tz_convert(UK)
    df["rain"] = pd.to_numeric(df["rain"], errors="coerce")  # nulls become NaN
    return df


def to_uk(ms):
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UK)


def has_time(dt) -> bool:
    """True for a real datetime; False for None or pandas NaT."""
    return dt is not None and not pd.isna(dt)


def fmt(dt) -> str:
    return dt.strftime("%a %d %b %Y, %H:%M") if has_time(dt) else "unknown"


def fmt_duration(start, end) -> str:
    if not has_time(start) or not has_time(end) or end < start:
        return ""
    mins = int((end - start).total_seconds() // 60)
    days, rem = divmod(mins, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {mins} min"
    return f"{mins} min"


def build_table(rows: list[dict], include_windermere: bool) -> pd.DataFrame:
    wanted = CATCHMENT | (WINDERMERE if include_windermere else set())
    records = []
    for a in rows:
        water = str(a.get("ReceivingWaterCourse") or "").strip()
        if water.lower() not in wanted:
            continue
        status = a.get("Status")
        status = status if status in STATUS else -1
        status_start = to_uk(a.get("StatusStart"))
        ev_start = to_uk(a.get("LatestEventStart"))
        # LatestEventEnd is sometimes empty in UU's feed; StatusStart is then the stop time
        ev_end = to_uk(a.get("LatestEventEnd")) or (status_start if status == 0 else None)
        records.append(
            {
                "id": a.get("Id"),
                "name": SITE_NAMES.get(a.get("Id"), a.get("Id")),
                "water": water,
                "status": status,
                "since": status_start,
                "ev_start": ev_start,
                "ev_end": ev_end,
                "feed_updated": to_uk(a.get("LastUpdated")),
                "lat": a.get("Latitude"),
                "lon": a.get("Longitude"),
            }
        )
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values(["status", "ev_start"], ascending=[False, False])
    return df


# --------------------------------------------------------------------- UI --

st.set_page_config(page_title="Cunsey Beck storm overflows", page_icon="💧", layout="centered")

st.title("Cunsey Beck storm overflows")
st.write(
    "Live status of United Utilities storm overflows discharging into Cunsey Beck "
    "and its headwaters at Hawkshead, from the National Storm Overflow Hub."
)

col_btn, col_opt = st.columns([1, 2])
with col_btn:
    if st.button("Refresh", type="primary", use_container_width=True):
        fetch_overflows.clear()
        fetch_rain.clear()
with col_opt:
    include_windermere = st.checkbox("Include overflows straight into Windermere")

try:
    rows, fetched_at = fetch_overflows()
except Exception as e:  # noqa: BLE001
    st.error(
        f"Couldn't reach the United Utilities feed: {e}. "
        "Press Refresh to try again in a minute."
    )
    st.stop()

df = build_table(rows, include_windermere)

feed_times = df["feed_updated"].dropna() if not df.empty else pd.Series(dtype=object)
feed_updated = max(feed_times) if len(feed_times) else None
st.caption(
    f"**Last updated:** {fmt(fetched_at.astimezone(UK))} (checked by this app) · "
    f"feed last reported {fmt(feed_updated)}"
)

if df.empty:
    st.info("No overflows found for this area. Press Refresh, or include Windermere.")
    st.stop()

# Overall summary
active = df[df["status"] == 1]
offline = df[df["status"] == -1]
if len(active):
    st.error(f"{len(active)} overflow(s) discharging now: " + ", ".join(active["name"]))
elif len(offline):
    st.warning("No discharges reported, but at least one monitor is offline.")
else:
    st.success("No overflows are discharging right now.")

# Rain in the next 24 hours
st.subheader("Rain in the next 24 hours")
try:
    rain = fetch_rain()
    now = pd.Timestamp.now(tz=UK).floor("h")
    next24 = rain[(rain["time"] >= now) & (rain["time"] < now + pd.Timedelta(hours=24))]
    known = next24.dropna(subset=["rain"])
    if known.empty:
        st.info("The forecast has no rain values for the next 24 hours.")
    else:
        total = known["rain"].sum()
        wet = known[known["rain"] >= WET_HOUR_MM]
        if wet.empty:
            st.success("No rain forecast for the next 24 hours.")
        else:
            first = wet["time"].iloc[0]
            peak = known.loc[known["rain"].idxmax()]
            msg = (
                f"Rain expected from {first.strftime('%a %H:%M')}: "
                f"{total:.1f} mm in total, heaviest {peak['rain']:.1f} mm "
                f"around {peak['time'].strftime('%a %H:%M')}."
            )
            if total >= HEAVY_24H_MM:
                st.warning(msg + " Heavy rain like this can make the overflows operate.")
            else:
                st.info(msg)
        # naive local times keep the bars in time order on the chart
        chart = known.assign(hour=known["time"].dt.tz_localize(None)).set_index("hour")["rain"]
        st.bar_chart(chart, y_label="Rain (mm per hour)", x_label="", height=200)
    st.caption("Forecast: Open-Meteo, ECMWF IFS model, for Near Sawrey.")
except Exception as e:  # noqa: BLE001
    st.warning(f"Couldn't load the rain forecast: {e}. Press Refresh to try again.")

# One panel per overflow
for _, r in df.iterrows():
    label, colour, _ = STATUS[r["status"]]
    with st.container(border=True):
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;align-items:baseline;gap:1rem'>"
            f"<strong style='font-size:1.1rem'>{r['name']}</strong>"
            f"<span style='color:{colour};font-weight:600'>{label}</span></div>"
            f"<div style='opacity:.75'>{r['id']} into {r['water']}</div>",
            unsafe_allow_html=True,
        )
        if r["status"] == 1:
            st.write(f"Discharging since {fmt(r['since'] if has_time(r['since']) else r['ev_start'])}")
        else:
            dur = fmt_duration(r["ev_start"], r["ev_end"])
            st.write(
                f"Last discharge: {fmt(r['ev_start'])} to {fmt(r['ev_end'])}"
                + (f" ({dur})" if dur else "")
            )

# Map
map_df = df.dropna(subset=["lat", "lon"]).copy()
if not map_df.empty:
    map_df["color"] = map_df["status"].map(lambda s: STATUS[s][1])
    st.map(map_df, latitude="lat", longitude="lon", color="color", size=60, zoom=11)

st.caption(
    "Data is near real-time and unverified. It shows when an overflow operates, "
    "not how much it releases or the water quality."
)
