
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque
import json
import math
import time
import uuid
import threading

import pandas as pd
import streamlit as st
import paho.mqtt.client as mqtt
from streamlit_autorefresh import st_autorefresh



# Default dashboard settings
if "vv_electricity_rate" not in st.session_state:
    st.session_state["vv_electricity_rate"] = 8.0

if "vv_refresh_seconds" not in st.session_state:
    st.session_state["vv_refresh_seconds"] = 5

# Current settings used throughout the dashboard
electricity_rate = float(
    st.session_state["vv_electricity_rate"]
)

refresh_seconds = int(
    st.session_state["vv_refresh_seconds"]
)


# =========================================================
# VOLTVISION AI CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
LIVE_FILE = BASE_DIR / "live_data.json"
HISTORY_FILE = BASE_DIR / "live_history.jsonl"

DEFAULT_WATTS = {
    "light": 10.0,
    "fan": 75.0,
    "motor": 100.0,
}

MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "nilm-expo/demo-7f3a91/telemetry"

TNPDCL_HOME = "https://www.tnpdcl.org/en/tnpdcl/"
TNPDCL_SERVICES = (
    "https://www.tnpdcl.org/en/tnpdcl/online-services/online-sevices/"
)
TNEB_PORTAL = "https://www.tnebnet.org/awp/login"

st.set_page_config(
    page_title="VoltVision AI",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================================================
# DASHBOARD STYLING
# =========================================================

st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg, #edf4ff, #f8faff 60%, #e7f8f1);
    color: #17243b;
}
[data-testid="stHeader"] {
    background: rgba(255,255,255,0.96);
}
.block-container {
    padding-top: 1.5rem;
    padding-bottom: 2rem;
    max-width: 1500px;
}
h1, h2, h3, h4 {
    color: #12213a !important;
    font-weight: 800 !important;
}
.stApp p, .stApp label, .stMarkdown, .stMarkdown p {
    color: #253650;
}
[data-testid="stCaptionContainer"] p {
    color: #526782 !important;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #101b35, #172f52);
}
[data-testid="stSidebar"] * {
    color: #f4f7ff;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: #cbd8f1;
}
.profile-card {
    background: linear-gradient(115deg, #123c69, #087f8c);
    color: white;
    padding: 24px;
    border-radius: 18px;
    margin: 8px 0 22px;
    box-shadow: 0 8px 24px rgba(18,60,105,0.20);
}
.profile-card h2, .profile-card p {
    color: white !important;
    margin: 5px 0;
}
[data-testid="stMetric"] {
    background: #ffffff !important;
    border: 1px solid #d8e3f0 !important;
    border-radius: 15px !important;
    padding: 17px !important;
    box-shadow: 0 5px 16px rgba(30,64,110,0.07);
}
[data-testid="stMetricLabel"] p {
    color: #526782 !important;
    font-weight: 650 !important;
}
[data-testid="stMetricValue"] {
    color: #123c69 !important;
    font-weight: 800 !important;
}
.stButton > button,
.stDownloadButton > button,
.stLinkButton > a {
    background: linear-gradient(100deg, #1264d8, #087f8c) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    padding: 0.65rem 1rem !important;
}
.stTextInput input, .stNumberInput input {
    background: white !important;
    color: #17243b !important;
    border: 1px solid #bdcde0 !important;
    border-radius: 9px !important;
}
[data-testid="stDataFrame"] {
    border: 1px solid #d8e3f0;
    border-radius: 12px;
    overflow: hidden;
}
hr {
    border-color: #d7e2ef !important;
}
@media (max-width: 768px) {
    .block-container {
        padding: 1rem 0.7rem;
    }
    [data-testid="stMetric"] {
        padding: 10px !important;
    }
}
</style>
""", unsafe_allow_html=True)

# =========================================================
# HELPER FUNCTIONS
# =========================================================

def get_value(data, names, default=None):
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return default


def to_number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "true", "yes", "1", "on", "running"
        }
    return False


def normalize(raw):
    raw_time = get_value(
        raw, ("received_at", "timestamp", "ts", "time"), time.time()
    )

    if isinstance(raw_time, str):
        try:
            raw_time = datetime.fromisoformat(
                raw_time.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            raw_time = to_number(raw_time, time.time())

    timestamp = to_number(raw_time, time.time())

    if timestamp > 100_000_000_000:
        timestamp /= 1000
    if timestamp <= 0:
        timestamp = time.time()

    light_on = to_bool(get_value(raw, ("light_on", "bulb_on"), False))
    fan_on = to_bool(get_value(raw, ("fan_on", "fan_running"), False))
    motor_on = to_bool(get_value(raw, ("motor_on", "pump_on"), False))

    light_w = max(0, to_number(get_value(
        raw, ("light_w", "light_watts", "light_power"),
        DEFAULT_WATTS["light"] if light_on else 0
    )))
    fan_w = max(0, to_number(get_value(
        raw, ("fan_w", "fan_watts", "fan_power"),
        DEFAULT_WATTS["fan"] if fan_on else 0
    )))
    motor_w = max(0, to_number(get_value(
        raw, ("motor_w", "motor_watts", "motor_power"),
        DEFAULT_WATTS["motor"] if motor_on else 0
    )))

    total_raw = get_value(
        raw,
        ("total_w", "total_watts", "total_power", "power_w", "watts"),
        None
    )

    total_w = (
        max(0, to_number(total_raw))
        if total_raw is not None
        else light_w + fan_w + motor_w
    )

    alert = str(get_value(raw, ("alert", "anomaly", "warning"), "") or "")
    if alert.strip().lower() in {
        "", "none", "false", "0", "normal", "ok"
    }:
        alert = ""

    return {
        "timestamp": timestamp,
        "datetime": datetime.fromtimestamp(timestamp),
        "light_on": light_on,
        "fan_on": fan_on,
        "motor_on": motor_on,
        "light_w": light_w,
        "fan_w": fan_w,
        "motor_w": motor_w,
        "total_w": total_w,
        "alert": alert,
    }


def energy_kwh(frame, column="total_w"):
    """Estimate energy from timestamped power samples."""
    if len(frame) < 2:
        return 0.0

    seconds = frame["timestamp"].diff().fillna(0).clip(
        lower=0, upper=3600
    )
    previous_watts = frame[column].shift(1).fillna(0)

    return float((previous_watts * seconds / 3_600_000).sum())


# =========================================================
# HIVEMQ MQTT RECEIVER
# =========================================================

@st.cache_resource
def start_mqtt_receiver():
    state = {
        "latest": None,
        "history": deque(maxlen=2000),
        "connected": False,
        "error": "",
        "lock": threading.Lock(),
    }

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            with state["lock"]:
                state["connected"] = True
                state["error"] = ""
            client.subscribe(MQTT_TOPIC)
        else:
            with state["lock"]:
                state["connected"] = False
                state["error"] = f"MQTT connection failed: {rc}"

    def on_disconnect(client, userdata, rc):
        with state["lock"]:
            state["connected"] = False

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                return

            payload["timestamp"] = time.time()

            with state["lock"]:
                state["latest"] = payload
                state["history"].append(payload)
                state["error"] = ""

        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            with state["lock"]:
                state["error"] = "Received invalid MQTT JSON data."

    client = mqtt.Client(
        client_id="VoltVisionDashboard-" + uuid.uuid4().hex[:10]
    )
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()
    except Exception as exc:
        with state["lock"]:
            state["error"] = str(exc)

    return {"client": client, "state": state}


mqtt_receiver = start_mqtt_receiver()

# =========================================================
# DEMO LOGIN
# =========================================================

if "vv_logged_in" not in st.session_state:
    st.session_state.vv_logged_in = False

if not st.session_state.vv_logged_in:
    st.title("⚡ VoltVision AI")
    st.subheader("Smart Electricity Monitoring")
    st.write("Sign in to view your personal energy dashboard.")

    with st.form("vv_login_form"):
        consumer_name = st.text_input("Consumer name")
        service_number = st.text_input("EB service connection number")
        login_clicked = st.form_submit_button(
            "Sign in", use_container_width=True
        )

    if login_clicked:
        if consumer_name.strip() and service_number.strip():
            st.session_state.vv_name = consumer_name.strip()
            st.session_state.vv_service_number = service_number.strip()
            st.session_state.vv_logged_in = True
            st.rerun()
        else:
            st.error("Please enter both fields.")

    st.info(
        "Demo login only: this form does not verify identity with EB records."
    )
    st.stop()


# =========================================================
# PROFILE AND NAVIGATION
# =========================================================

profile_col, logout_col = st.columns([5, 1])

with profile_col:
    st.markdown(
        f'<div class="profile-card">'
        f'<h2>👤 {st.session_state.vv_name}</h2>'
        f'<p>EB Service No: {st.session_state.vv_service_number}</p>'
        f'<p>⚡ VoltVision AI Consumer Dashboard</p>'
        f'</div>',
        unsafe_allow_html=True
    )

with logout_col:
    if st.button("Log out", key="vv_logout"):
        st.session_state.vv_logged_in = False
        st.rerun()

st.sidebar.title("⚡ VoltVision AI")
st.sidebar.caption("SMART ENERGY • LIVE MONITORING")

page = st.sidebar.radio(
    "Navigate",
    [
        "Home",
        "Appliance Monitoring",
        "Energy Reports",
        "Alerts",
        "Savings Advisor",
        "EB Services",
        "Settings",
    ],
    key="vv_navigation",
)

st.sidebar.divider()

auto_refresh = st.sidebar.checkbox(
    "Auto-refresh telemetry", value=True, key="vv_auto_refresh"
)

refresh_seconds = st.sidebar.slider(
    "Refresh interval (seconds)",
    min_value=2,
    max_value=30,
    value=5,
    key="vv_refresh_interval",
)

tariff = st.sidebar.number_input(
    "Estimated tariff (₹/kWh)",
    min_value=0.0,
    max_value=1000.0,
    value=8.0,
    step=0.5,
    key="vv_tariff",
)

load_limit = st.sidebar.number_input(
    "High load alert (W)",
    min_value=1.0,
    max_value=100000.0,
    value=250.0,
    step=10.0,
    key="vv_load_limit",
)

daily_budget = st.sidebar.number_input(
    "Daily estimated budget (₹)",
    min_value=0.0,
    max_value=100000.0,
    value=50.0,
    step=5.0,
    key="vv_daily_budget",
)

if auto_refresh:
    st_autorefresh(
        interval=int(refresh_seconds * 1000),
        key="vv_refresh_timer",
    )


# =========================================================
# READ LIVE DATA FROM HIVEMQ
# =========================================================

mqtt_state = mqtt_receiver["state"]

with mqtt_state["lock"]:
    mqtt_history = list(mqtt_state["history"])
    mqtt_latest = (
        dict(mqtt_state["latest"])
        if mqtt_state["latest"] is not None
        else None
    )
    mqtt_connected = mqtt_state["connected"]
    mqtt_error = mqtt_state["error"]

records = []

# Load previously stored history, if available.
if HISTORY_FILE.exists():
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    item = json.loads(line.strip())
                    if isinstance(item, dict):
                        records.append(normalize(item))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except OSError:
        pass

# Add telemetry received since this dashboard started.
for item in mqtt_history:
    records.append(normalize(item))

latest = normalize(mqtt_latest) if mqtt_latest else None

# Remove duplicate records.
unique = {}
for row in records:
    key = (
        row["timestamp"],
        row["light_w"],
        row["fan_w"],
        row["motor_w"],
    )
    unique[key] = row

records = sorted(
    unique.values(),
    key=lambda row: row["timestamp"]
)

if latest is not None:
    if not records or latest["timestamp"] > records[-1]["timestamp"]:
        records.append(latest)

if latest is None and records:
    latest = records[-1]

# Show connection status.
if mqtt_connected:
    st.sidebar.success("HiveMQ connected")
else:
    st.sidebar.warning("Connecting to HiveMQ...")

if mqtt_error:
    st.sidebar.caption(mqtt_error)

# Stop gracefully if no telemetry has arrived.
if not records:
    st.title("⚡ VoltVision AI")
    st.subheader("Waiting for Live Telemetry")

    st.info(
        "The dashboard is waiting for data from your ESP32. "
        "Check the device Wi-Fi connection, MQTT connection, "
        "and MQTT topic."
    )

    st.code(MQTT_TOPIC)

    st.caption(
        "Keep the ESP32 powered on and publishing telemetry. "
        "The dashboard will check again automatically."
    )
    st.stop()

# Prepare the dashboard's data frame.
df = pd.DataFrame(records)
df = df.sort_values("timestamp").reset_index(drop=True)

latest_row = (
    latest if latest is not None
    else df.iloc[-1].to_dict()
)

# =========================================================
# ENERGY, COST, AND ALERT CALCULATIONS
# =========================================================

now = time.time()

age_seconds = max(
    0,
    now - float(latest_row["timestamp"])
)

today_start = datetime.now().replace(
    hour=0, minute=0, second=0, microsecond=0
).timestamp()

week_start = (
    datetime.now() - timedelta(days=7)
).timestamp()

today_df = df[df["timestamp"] >= today_start].copy()
week_df = df[df["timestamp"] >= week_start].copy()

today_kwh = energy_kwh(today_df)
week_kwh = energy_kwh(week_df)

today_cost = today_kwh * tariff
week_cost = week_kwh * tariff

appliance_columns = {
    "Light": "light_w",
    "Fan": "fan_w",
    "Motor": "motor_w",
}

appliance_kwh = {
    name: energy_kwh(today_df, column)
    for name, column in appliance_columns.items()
}

appliance_cost = {
    name: value * tariff
    for name, value in appliance_kwh.items()
}

alerts = []

if latest_row["total_w"] > load_limit:
    alerts.append(
        f"High load: {latest_row['total_w']:.1f} W exceeds "
        f"the {load_limit:.1f} W threshold."
    )

if age_seconds > max(30, refresh_seconds * 3):
    alerts.append(
        f"Telemetry may be stale. Last update was "
        f"{age_seconds:.0f} seconds ago."
    )

if latest_row.get("alert"):
    alerts.append(f"Device alert: {latest_row['alert']}")

if today_cost > daily_budget:
    alerts.append(
        f"Estimated daily cost ₹{today_cost:.2f} exceeds "
        f"budget ₹{daily_budget:.2f}."
    )

score = 100

if latest_row["total_w"] > load_limit:
    score -= 25

if today_cost > daily_budget:
    score -= 20

if age_seconds > max(30, refresh_seconds * 3):
    score -= 20

score = max(0, score)

observed_hours = 0.0

if len(today_df) >= 2:
    observed_hours = max(
        0,
        (
            today_df["timestamp"].max()
            - today_df["timestamp"].min()
        ) / 3600,
    )

estimated_daily_cost = (
    today_cost * 24 / observed_hours
    if observed_hours >= 1 / 60
    else today_cost
)

monthly_bill = estimated_daily_cost * 30
potential_saving = monthly_bill * 0.10

# =========================================================
# HOME
# =========================================================

if page == "Home":
    st.title("⚡ VoltVision AI")
    st.subheader("Smart Energy Command Center")

    st.write(
        f"Welcome back, **{st.session_state.vv_name}**! "
        "Here is your electricity usage at a glance."
    )

    if age_seconds <= max(30, refresh_seconds * 3):
        st.success("Recent telemetry is available.")
    else:
        st.warning(
            f"Waiting for fresh MQTT telemetry. "
            f"Last message: {age_seconds:.0f} seconds ago."
        )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Current Load", f"{latest_row['total_w']:.1f} W")
    c2.metric("Today's Energy", f"{today_kwh:.4f} kWh")
    c3.metric("Today's Estimated Cost", f"₹{today_cost:.2f}")
    c4.metric("Rule-Based Score", f"{score}/100")

    c1, c2, c3 = st.columns(3)

    c1.metric("Last 7 Days", f"{week_kwh:.4f} kWh")
    c2.metric("30-Day Bill Forecast", f"₹{monthly_bill:.2f}")
    c3.metric("Possible 10% Saving", f"₹{potential_saving:.2f}")

    st.subheader("Appliance Status")

    cols = st.columns(3)

    appliances = [
        ("Light", "light_on", "light_w"),
        ("Fan", "fan_on", "fan_w"),
        ("Motor", "motor_on", "motor_w"),
    ]

    for col, (name, on_key, watt_key) in zip(cols, appliances):
        with col:
            st.markdown(
                f"### {'🟢' if latest_row[on_key] else '⚪'} {name}"
            )
            st.metric("Power", f"{latest_row[watt_key]:.1f} W")
            st.write(
                "Status: **ON**"
                if latest_row[on_key]
                else "Status: **OFF**"
            )

    st.subheader("Recent Power Consumption")

    chart = df.copy()
    chart["Time"] = pd.to_datetime(chart["timestamp"], unit="s")
    chart = chart.set_index("Time")

    st.line_chart(
        chart[["light_w", "fan_w", "motor_w", "total_w"]].rename(
            columns={
                "light_w": "Light (W)",
                "fan_w": "Fan (W)",
                "motor_w": "Motor (W)",
                "total_w": "Total Load (W)",
            }
        )
    )


# =========================================================
# APPLIANCE MONITORING
# =========================================================

elif page == "Appliance Monitoring":
    st.title("🔌 Appliance-wise Monitoring")
    st.caption("Power, estimated energy and cost by appliance.")

    cols = st.columns(3)

    appliances = [
        ("Light", "light_on", "light_w"),
        ("Fan", "fan_on", "fan_w"),
        ("Motor", "motor_on", "motor_w"),
    ]

    for col, (name, on_key, watt_key) in zip(cols, appliances):
        with col:
            st.subheader(name)

            st.write(
                "🟢 ON" if latest_row[on_key] else "⚪ OFF"
            )

            st.metric(
                "Current Power",
                f"{latest_row[watt_key]:.1f} W"
            )

            st.metric(
                "Today's Energy",
                f"{appliance_kwh[name]:.4f} kWh"
            )

            st.metric(
                "Estimated Cost",
                f"₹{appliance_cost[name]:.2f}"
            )

    st.subheader("Appliance Energy Comparison")

    st.bar_chart(
        pd.DataFrame.from_dict(
            appliance_kwh,
            orient="index",
            columns=["Energy (kWh)"],
        )
    )

    st.subheader("Appliance Cost Comparison")

    st.bar_chart(
        pd.DataFrame.from_dict(
            appliance_cost,
            orient="index",
            columns=["Cost (₹)"],
        )
    )


# =========================================================
# ENERGY REPORTS
# =========================================================

elif page == "Energy Reports":
    st.title("📊 Energy Reports")
    st.caption("VoltVision estimates, not official EB bill figures.")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Today's Energy", f"{today_kwh:.4f} kWh")
    c2.metric("Today's Cost", f"₹{today_cost:.2f}")
    c3.metric("Last 7 Days", f"{week_kwh:.4f} kWh")
    c4.metric("7-Day Cost", f"₹{week_cost:.2f}")

    st.metric("Estimated 30-Day Bill", f"₹{monthly_bill:.2f}")
    st.metric("Illustrative 10% Saving", f"₹{potential_saving:.2f}")

    report_columns = [
        "datetime",
        "light_on",
        "light_w",
        "fan_on",
        "fan_w",
        "motor_on",
        "motor_w",
        "total_w",
        "alert",
    ]

    report_df = df[report_columns].sort_values(
        "datetime",
        ascending=False,
    )

    st.dataframe(
        report_df.head(200),
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "⬇ Download VoltVision Report (CSV)",
        data=df[report_columns].to_csv(index=False).encode("utf-8"),
        file_name="voltvision_energy_report.csv",
        mime="text/csv",
        key="vv_report_download",
    )


# =========================================================
# ALERTS
# =========================================================

elif page == "Alerts":
    st.title("🚨 Anomaly Detection and Alerts")

    if alerts:
        for message in alerts:
            st.warning(message)
    else:
        st.success(
            "No configured rule-based alerts detected right now."
        )

    st.caption(
        "This prototype uses thresholds and rules; "
        "it does not train an AI model."
    )

    alert_df = df[
        df["alert"].astype(str).str.strip() != ""
    ]

    st.subheader("Device-Reported Alert History")

    if alert_df.empty:
        st.write(
            "No device-reported alerts in the available history."
        )
    else:
        st.dataframe(
            alert_df[["datetime", "total_w", "alert"]].tail(100),
            use_container_width=True,
            hide_index=True,
        )

# =========================================================
# SAVINGS ADVISOR
# =========================================================

elif page == "Savings Advisor":
    st.title("💡 Smart Savings Advisor")

    st.write(
        "Explore simple ways to reduce electricity usage "
        "based on the current appliance readings."
    )

    st.subheader("Estimated Appliance Consumption")

    for name, on_key, watt_key in [
        ("Light", "light_on", "light_w"),
        ("Fan", "fan_on", "fan_w"),
        ("Motor", "motor_on", "motor_w"),
    ]:
        power = float(latest_row[watt_key])
        is_on = bool(latest_row[on_key])

        if is_on and power > 0:
            st.info(
                f"**{name}:** Currently using approximately "
                f"{power:.1f} W. Turn it off when it is not needed."
            )
        elif is_on:
            st.info(
                f"**{name}:** Marked ON, but reported power is "
                f"{power:.1f} W. Verify the device reading."
            )
        else:
            st.success(f"**{name}:** Currently OFF.")

    st.subheader("Estimate Your Potential Savings")

    daily_hours = st.slider(
        "Hours per day you could reduce usage",
        min_value=0,
        max_value=12,
        value=2,
        key="vv_saving_hours",
    )

    reduction_percent = st.slider(
        "Estimated reduction in appliance usage (%)",
        min_value=0,
        max_value=100,
        value=10,
        step=5,
        key="vv_saving_percent",
    )

    current_power_kw = max(
        float(latest_row["total_w"]), 0.0
    ) / 1000.0

    estimated_saved_kwh = (
        current_power_kw
        * daily_hours
        * 30
        * reduction_percent
        / 100
    )

    estimated_saved_rupees = (
        estimated_saved_kwh * electricity_rate
    )

    c1, c2 = st.columns(2)

    c1.metric(
        "Illustrative Monthly Energy Saving",
        f"{estimated_saved_kwh:.2f} kWh",
    )

    c2.metric(
        "Illustrative Monthly Cost Saving",
        f"₹{estimated_saved_rupees:.2f}",
    )

    st.caption(
        "This is a what-if estimate based on the current reported "
        "load, the selected hours, and the assumed reduction. "
        "Actual savings depend on changing appliance usage and "
        "your electricity tariff."
    )


# =========================================================
# EB SERVICES
# =========================================================



elif page == "EB Services":
    import streamlit as st

    st.title("🏢 Tamil Nadu EB Services")
    st.write(
        "Access official Tamil Nadu electricity services "
        "and prepare your consumer bill enquiry."
    )

    # Official TNPDCL links
    TNPDCL_HOME = "https://www.tnpdcl.org/en/tnpdcl/"
    TNPDCL_SERVICES = (
        "https://www.tnpdcl.org/en/tnpdcl/"
        "online-services/online-sevices/"
    )
    TNPDCL_PAYMENT = "https://www.tnebnet.org/awp/login"

    st.subheader("🌐 Official Electricity Board Websites")

    st.success(
        "Use the buttons below to open the official websites "
        "in your browser."
    )

    st.link_button(
        "🏠 Open Official TNPDCL Website",
        TNPDCL_HOME,
        use_container_width=True,
    )

    st.link_button(
        "📋 Bill Status, Complaints & Other Services",
        TNPDCL_SERVICES,
        use_container_width=True,
    )

    st.link_button(
        "💳 Open Electricity Bill Payment Portal",
        TNPDCL_PAYMENT,
        use_container_width=True,
    )

    st.caption(
        "These links open external websites. You may need "
        "your electricity service connection number to use "
        "some services."
    )

    st.divider()

    st.subheader("👤 Consumer Information")

    consumer_name = st.text_input(
        "Consumer name",
        key="eb_consumer_name",
    )

    consumer_number = st.text_input(
        "EB Service Connection Number",
        key="eb_consumer_number",
        help="Enter your service number only if you wish to "
             "include it in your downloaded notes.",
    )

    service_region = st.text_input(
        "Area / Section",
        key="eb_service_region",
    )

    st.divider()

    st.subheader("🧾 Electricity Bill Estimate")

    eb_rate = st.number_input(
        "Estimated tariff (₹ per kWh)",
        min_value=0.0,
        max_value=100.0,
        value=float(
            st.session_state.get("vv_electricity_rate", 8.0)
        ),
        step=0.5,
        key="eb_estimated_rate",
    )

    # Use the existing energy calculation from your dashboard.
    energy_today = float(today_kwh)
    estimated_charge = energy_today * eb_rate

    col1, col2 = st.columns(2)

    col1.metric(
        "Today's Energy",
        f"{energy_today:.3f} kWh",
    )

    col2.metric(
        "Estimated Energy Charge",
        f"₹{estimated_charge:.2f}",
    )

    st.caption(
        "This is an estimate, not your official EB bill. "
        "Actual bills may include tariff slabs, fixed charges, "
        "taxes, and other adjustments."
    )

    st.divider()

    st.subheader("📝 Bill Enquiry Notes")

    enquiry_notes = st.text_area(
        "Notes or questions for your electricity provider",
        placeholder=(
            "Example: Please help me check my bill amount "
            "or confirm my applicable tariff."
        ),
        height=120,
        key="eb_enquiry_notes",
    )

    report = f"""
VOLT VISION AI - EB CONSUMER ENQUIRY
====================================

Consumer name: {consumer_name}
Service connection number: {consumer_number}
Area / Section: {service_region}

Today's monitored energy: {energy_today:.3f} kWh
Estimated tariff: Rs. {eb_rate:.2f} per kWh
Estimated energy charge: Rs. {estimated_charge:.2f}

Enquiry notes:
{enquiry_notes}

Official TNPDCL website:
{TNPDCL_HOME}

Official online services:
{TNPDCL_SERVICES}

Bill payment portal:
{TNPDCL_PAYMENT}

Note: This is a project-generated estimate, not an official bill.
"""

    st.download_button(
        label="⬇️ Download Consumer Enquiry Report",
        data=report,
        file_name="VoltVision_EB_Enquiry.txt",
        mime="text/plain",
        use_container_width=True,
        key="eb_download_report",
    )

    st.info(
        "VoltVision AI does not directly connect to TNPDCL "
        "consumer accounts or submit bill enquiries. Use the "
        "official links above for actual electricity services."
    )


# =========================================================
# SETTINGS
# =========================================================

elif page == "Settings":
    st.title("⚙️ Settings")

    st.subheader("Dashboard Preferences")

    selected_rate = st.number_input(
        "Electricity tariff (₹ per kWh)",
        min_value=0.0,
        max_value=100.0,
        value=float(electricity_rate),
        step=0.5,
        key="vv_settings_rate",
    )

    selected_refresh = st.selectbox(
        "Dashboard refresh interval",
        options=[2, 5, 10, 15, 30],
        index=1,
        format_func=lambda value: f"{value} seconds",
        key="vv_settings_refresh",
    )

    st.caption(
        "The tariff and refresh interval selected here apply "
        "to this Streamlit session after you click Save."
    )

    if st.button("💾 Save Settings", key="vv_save_settings"):
        st.session_state["vv_electricity_rate"] = float(
            selected_rate
        )
        st.session_state["vv_refresh_seconds"] = int(
            selected_refresh
        )

        st.success(
            "Settings saved for this session. "
            "The page will rerun using the selected values."
        )
        st.rerun()

    st.divider()

    st.subheader("MQTT Connection")

    st.write(f"**Broker:** `{MQTT_BROKER}`")
    st.write(f"**Port:** `{MQTT_PORT}`")
    st.write(f"**Topic:** `{MQTT_TOPIC}`")

    if mqtt_receiver["state"]["connected"]:
        st.success("MQTT receiver reports a connected session.")
    else:
        st.warning(
            "MQTT is not currently reporting a connected session. "
            "Check the broker and network connection."
        )

    st.caption(
        "The broker and topic are configured in the application "
        "code. Restart or redeploy the app after changing them."
    )

    st.divider()

    st.subheader("Session")

    st.write(
        f"Logged in as **{st.session_state.vv_name}**."
    )

    
if st.button("Log Out", key="vv_logout_settings"):
    st.session_state["vv_logged_in"] = False
    st.rerun()


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.markdown(
    """
    <div style="text-align:center; padding:10px;">
        <h4>⚡ VoltVision AI</h4>
        <p>Smart Electricity Monitoring and Anomaly Detection System</p>
        <p>ESP32 • MQTT • Streamlit • Energy Analytics</p>
        <small>
            Academic project prototype. Energy costs and savings
            are estimates, not official billing figures.
        </small>
    </div>
    """,
    unsafe_allow_html=True,
)
