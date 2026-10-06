import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

STATUS_FILE = BASE_DIR / "parking_status.json"
DETECTION_SCRIPT = BASE_DIR / "detection.py"

RAW_VIDEO = BASE_DIR / "files" / "parking_output.mp4"
WEB_VIDEO = BASE_DIR / "files" / "parking_output_web.mp4"

DATABASE_FILE = BASE_DIR / "smartpark.db"


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SmartPark AI",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# SESSION STATE
# ============================================================

if "detector_process" not in st.session_state:
    st.session_state.detector_process = None


# ============================================================
# SIMPLE STYLING
# ============================================================

st.markdown(
    """
<style>
.block-container {
    padding-top: 1.5rem;
    padding-bottom: 4rem;
    max-width: 1450px;
}

[data-testid="stMetric"] {
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 18px;
    border-radius: 16px;
}

[data-testid="stSidebar"] {
    border-right: 1px solid rgba(255,255,255,0.06);
}

footer {
    visibility: hidden;
}
</style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# DATABASE
# ============================================================

def get_connection():

    return sqlite3.connect(
        DATABASE_FILE,
        timeout=5
    )


def initialize_database():

    with get_connection() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS parking_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                total INTEGER NOT NULL,
                available INTEGER NOT NULL,
                occupied INTEGER NOT NULL,
                reserved INTEGER NOT NULL,
                free INTEGER NOT NULL,
                occupancy REAL NOT NULL,
                utilization REAL NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot INTEGER NOT NULL,
                customer_name TEXT,
                vehicle_number TEXT,
                reserved_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            )
            """
        )

        conn.commit()


initialize_database()


# ============================================================
# STATUS HELPERS
# ============================================================

def default_status():

    return {
        "running": False,
        "updated_at": "Not available",
        "total": 14,
        "available": 0,
        "occupied": 0,
        "occupancy": 0.0,
        "slots": [],
    }


def read_status():

    if not STATUS_FILE.exists():
        return default_status()

    try:

        with open(
            STATUS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception:

        return default_status()


def detector_is_live(status):

    if not status.get(
        "running",
        False
    ):
        return False

    updated_at = status.get(
        "updated_at"
    )

    if not updated_at:
        return False

    try:

        last_update = datetime.fromisoformat(
            updated_at
        )

        age = (
            datetime.now()
            -
            last_update
        ).total_seconds()

        return age < 5

    except Exception:

        return False


# ============================================================
# RESERVATIONS
# ============================================================

def get_active_reservations():

    with get_connection() as conn:

        rows = conn.execute(
            """
            SELECT
                id,
                slot,
                customer_name,
                vehicle_number,
                reserved_at
            FROM reservations
            WHERE status = 'ACTIVE'
            ORDER BY slot
            """
        ).fetchall()

    result = {}

    for row in rows:

        result[int(row[1])] = {
            "id": row[0],
            "slot": row[1],
            "customer_name": row[2],
            "vehicle_number": row[3],
            "reserved_at": row[4],
        }

    return result


def create_reservation(
    slot,
    customer_name,
    vehicle_number
):

    active = get_active_reservations()

    if slot in active:
        return False, "This parking slot is already reserved."

    with get_connection() as conn:

        conn.execute(
            """
            INSERT INTO reservations (
                slot,
                customer_name,
                vehicle_number,
                reserved_at,
                status
            )
            VALUES (?, ?, ?, ?, 'ACTIVE')
            """,
            (
                slot,
                customer_name.strip(),
                vehicle_number.strip().upper(),
                datetime.now().isoformat(
                    timespec="seconds"
                ),
            )
        )

        conn.commit()

    return True, f"Slot {slot} reserved successfully."


def cancel_reservation(
    reservation_id
):

    with get_connection() as conn:

        conn.execute(
            """
            UPDATE reservations
            SET status = 'CANCELLED'
            WHERE id = ?
            """,
            (
                reservation_id,
            )
        )

        conn.commit()


# ============================================================
# EFFECTIVE PARKING COUNTS
# ============================================================

def calculate_effective_status(
    status
):

    total = int(
        status.get(
            "total",
            14
        )
    )

    slots = status.get(
        "slots",
        []
    )

    reservations = (
        get_active_reservations()
    )

    ai_empty_slots = set()

    occupied = 0


    for slot in slots:

        number = int(
            slot.get(
                "slot",
                0
            )
        )

        state = slot.get(
            "status",
            "UNKNOWN"
        )

        if state == "EMPTY":

            ai_empty_slots.add(
                number
            )

        elif state == "FULL":

            occupied += 1


    reserved_slots = set(
        reservations.keys()
    )

    reserved_empty = (
        ai_empty_slots
        &
        reserved_slots
    )

    free_slots = (
        ai_empty_slots
        -
        reserved_slots
    )


    if not slots:

        occupied = int(
            status.get(
                "occupied",
                0
            )
        )

        ai_available = int(
            status.get(
                "available",
                0
            )
        )

        free_count = max(
            ai_available
            -
            len(
                reserved_slots
            ),
            0
        )

        reserved_for_utilization = min(
            len(
                reserved_slots
            ),
            ai_available
        )

    else:

        free_count = len(
            free_slots
        )

        reserved_for_utilization = len(
            reserved_empty
        )


    occupancy = (
        (occupied / total) * 100
        if total > 0
        else 0
    )


    utilization = (
        (
            occupied
            +
            reserved_for_utilization
        )
        /
        total
        * 100
        if total > 0
        else 0
    )


    return {
        "total": total,
        "free": free_count,
        "occupied": occupied,
        "reserved": len(
            reserved_slots
        ),
        "occupancy": round(
            occupancy,
            1
        ),
        "utilization": round(
            utilization,
            1
        ),
        "reservations": reservations,
    }


# ============================================================
# PARKING HISTORY
# ============================================================

def log_history_if_needed():

    status = read_status()

    if not detector_is_live(
        status
    ):
        return


    effective = calculate_effective_status(
        status
    )


    with get_connection() as conn:

        latest = conn.execute(
            """
            SELECT
                timestamp,
                available,
                occupied,
                reserved
            FROM parking_history
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()


        should_insert = False


        if latest is None:

            should_insert = True

        else:

            try:

                last_time = datetime.fromisoformat(
                    latest[0]
                )

                seconds = (
                    datetime.now()
                    -
                    last_time
                ).total_seconds()

            except Exception:

                seconds = 999


            values_changed = (
                latest[1]
                != effective["free"]
                or
                latest[2]
                != effective["occupied"]
                or
                latest[3]
                != effective["reserved"]
            )


            if (
                seconds >= 10
                or
                values_changed
            ):

                should_insert = True


        if should_insert:

            conn.execute(
                """
                INSERT INTO parking_history (
                    timestamp,
                    total,
                    available,
                    occupied,
                    reserved,
                    free,
                    occupancy,
                    utilization
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    effective["total"],
                    effective["free"],
                    effective["occupied"],
                    effective["reserved"],
                    effective["free"],
                    effective["occupancy"],
                    effective["utilization"],
                )
            )

            conn.commit()


def get_history():

    with get_connection() as conn:

        dataframe = pd.read_sql_query(
            """
            SELECT
                timestamp,
                total,
                available,
                occupied,
                reserved,
                free,
                occupancy,
                utilization
            FROM parking_history
            ORDER BY id
            """,
            conn
        )

    if not dataframe.empty:

        dataframe[
            "timestamp"
        ] = pd.to_datetime(
            dataframe["timestamp"]
        )

    return dataframe


# ============================================================
# CURRENT REPORT
# ============================================================

def generate_current_report():

    status = read_status()

    reservations = (
        get_active_reservations()
    )

    rows = []


    for item in status.get(
        "slots",
        []
    ):

        number = int(
            item.get(
                "slot",
                0
            )
        )

        ai_status = item.get(
            "status",
            "UNKNOWN"
        )


        if number in reservations:

            final_status = (
                "RESERVED"
            )

            vehicle = reservations[
                number
            ].get(
                "vehicle_number",
                ""
            )

            customer = reservations[
                number
            ].get(
                "customer_name",
                ""
            )

        else:

            vehicle = ""
            customer = ""

            if ai_status == "EMPTY":

                final_status = (
                    "AVAILABLE"
                )

            elif ai_status == "FULL":

                final_status = (
                    "OCCUPIED"
                )

            else:

                final_status = (
                    "UNKNOWN"
                )


        rows.append(
            {
                "Parking Slot":
                    number,

                "Parking Row":
                    (
                        "Left"
                        if number <= 7
                        else "Right"
                    ),

                "AI Detection":
                    ai_status,

                "Final Status":
                    final_status,

                "Vehicle Number":
                    vehicle,

                "Reserved By":
                    customer,
            }
        )


    return pd.DataFrame(
        rows
    )


# ============================================================
# GLOBAL HISTORY UPDATE
# ============================================================

log_history_if_needed()


# ============================================================
# HEADER
# ============================================================

with st.container(
    border=True
):

    st.title(
        "🚗 SmartPark AI"
    )

    st.subheader(
        "AI-Based Smart Parking Slot "
        "Detection and Management System"
    )

    st.caption(
        "Computer Vision • Machine Learning • "
        "Reservations • Analytics • Smart Parking"
    )


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "🚗 SmartPark"
)

st.sidebar.caption(
    "AI Parking Management System"
)


page = st.sidebar.radio(
    "Navigation",
    [
        "🏠 Dashboard",
        "🤖 AI Detection",
        "🅿️ Parking Slots",
        "🎫 Reservations",
        "📊 Analytics",
        "📈 Results",
        "⚙️ System",
        "ℹ️ About Project",
    ]
)


st.sidebar.divider()


sidebar_status = read_status()


if detector_is_live(
    sidebar_status
):

    st.sidebar.success(
        "● AI SYSTEM ONLINE"
    )

else:

    st.sidebar.warning(
        "● AI SYSTEM OFFLINE"
    )


st.sidebar.caption(
    f"Last update: "
    f"{sidebar_status.get('updated_at', 'N/A')}"
)


effective_sidebar = (
    calculate_effective_status(
        sidebar_status
    )
)


st.sidebar.metric(
    "Free Spaces",
    effective_sidebar["free"]
)

st.sidebar.metric(
    "Active Reservations",
    effective_sidebar["reserved"]
)


# ============================================================
# COMMON DASHBOARD
# ============================================================

def render_dashboard():

    log_history_if_needed()

    status = read_status()

    effective = (
        calculate_effective_status(
            status
        )
    )


    total = effective[
        "total"
    ]

    free = effective[
        "free"
    ]

    occupied = effective[
        "occupied"
    ]

    reserved = effective[
        "reserved"
    ]

    utilization = effective[
        "utilization"
    ]


    # --------------------------------------------------------
    # METRICS
    # --------------------------------------------------------

    c1, c2, c3, c4, c5 = (
        st.columns(5)
    )


    with c1:

        st.metric(
            "🚗 Total Slots",
            total
        )


    with c2:

        st.metric(
            "🟢 Free",
            free
        )


    with c3:

        st.metric(
            "🟡 Reserved",
            reserved
        )


    with c4:

        st.metric(
            "🔴 Occupied",
            occupied
        )


    with c5:

        st.metric(
            "📊 Utilization",
            f"{utilization:.1f}%"
        )


    st.write("")


    # --------------------------------------------------------
    # VISUAL STATUS
    # --------------------------------------------------------

    left, right = st.columns(
        [2, 1]
    )


    with left:

        with st.container(
            border=True
        ):

            st.subheader(
                "📊 Parking Utilization"
            )

            progress = min(
                max(
                    utilization / 100,
                    0
                ),
                1
            )

            st.progress(
                progress
            )


            if free > 0:

                st.success(
                    f"🟢 {free} parking space(s) "
                    f"available for new vehicles."
                )

            else:

                st.error(
                    "🔴 No unreserved parking "
                    "spaces currently available."
                )


    with right:

        with st.container(
            border=True
        ):

            st.subheader(
                "🤖 AI System"
            )

            if detector_is_live(
                status
            ):

                st.success(
                    "ONLINE"
                )

            else:

                st.warning(
                    "OFFLINE"
                )

            st.caption(
                "Last detection update"
            )

            st.write(
                status.get(
                    "updated_at",
                    "Not available"
                )
            )


    # --------------------------------------------------------
    # STATUS CHART
    # --------------------------------------------------------

    st.subheader(
        "📉 Current Parking Distribution"
    )


    chart_data = pd.DataFrame(
        {
            "Status": [
                "Free",
                "Reserved",
                "Occupied",
            ],

            "Spaces": [
                free,
                reserved,
                occupied,
            ],
        }
    ).set_index(
        "Status"
    )


    st.bar_chart(
        chart_data
    )


# ============================================================
# DASHBOARD PAGE
# ============================================================

if page == "🏠 Dashboard":

    st.header(
        "📊 Smart Parking Dashboard"
    )

    st.caption(
        "Live parking availability, "
        "reservations and occupancy monitoring"
    )

    st.divider()


    if hasattr(
        st,
        "fragment"
    ):

        @st.fragment(
            run_every=2
        )
        def dashboard_fragment():

            render_dashboard()


        dashboard_fragment()


    else:

        render_dashboard()


# ============================================================
# AI DETECTION
# ============================================================

elif page == "🤖 AI Detection":

    st.header(
        "🤖 AI Parking Detection"
    )

    st.caption(
        "Control the OpenCV + SVM detection engine"
    )

    st.divider()


    status = read_status()

    running = detector_is_live(
        status
    )


    start_col, stop_col, status_col = (
        st.columns(
            [1, 1, 2]
        )
    )


    # START

    with start_col:

        if st.button(
            "▶ Start Detection",
            type="primary",
            use_container_width=True,
            disabled=running,
        ):

            try:

                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(
                            DETECTION_SCRIPT
                        ),
                    ],
                    cwd=str(
                        BASE_DIR
                    ),
                )

                st.session_state[
                    "detector_process"
                ] = process


                st.success(
                    "AI detector started."
                )

                time.sleep(
                    1
                )

                st.rerun()


            except Exception as error:

                st.error(
                    "Unable to start detection."
                )

                st.exception(
                    error
                )


    # STOP

    with stop_col:

        if st.button(
            "■ Stop Process",
            use_container_width=True
        ):

            process = st.session_state.get(
                "detector_process"
            )

            if (
                process is not None
                and
                process.poll() is None
            ):

                process.terminate()

                st.success(
                    "Detector process stopped."
                )

                time.sleep(
                    1
                )

                st.rerun()

            else:

                st.warning(
                    "Use Q inside the OpenCV "
                    "window to stop detection."
                )


    with status_col:

        if running:

            st.success(
                "✅ Detection Engine ONLINE"
            )

        else:

            st.warning(
                "⏸ Detection Engine OFFLINE"
            )


    st.info(
        """
        For the cleanest processed video,
        click the OpenCV window and press **Q**
        when you want to finish detection.
        """
    )


    st.divider()

    render_dashboard()


    st.divider()


    st.subheader(
        "🧠 Detection Pipeline"
    )


    st.code(
        """
Parking Video
      ↓
OpenCV Frame Processing
      ↓
14 Parking Regions
      ↓
Image Preprocessing
      ↓
SVM Classifier
      ↓
EMPTY / OCCUPIED
      ↓
parking_status.json
      ↓
SmartPark Application
        """
    )


# ============================================================
# PARKING SLOTS
# ============================================================

elif page == "🅿️ Parking Slots":

    st.header(
        "🅿️ Live Parking Slot Map"
    )

    st.caption(
        "Green = Free • Yellow = Reserved • Red = Occupied"
    )

    st.divider()


    status = read_status()

    reservations = (
        get_active_reservations()
    )


    slots = status.get(
        "slots",
        []
    )


    if not slots:

        st.info(
            "Start AI Detection first."
        )

    else:

        left_col, right_col = (
            st.columns(2)
        )


        with left_col:

            st.subheader(
                "⬅️ Left Parking Row"
            )


            for item in slots:

                number = int(
                    item.get(
                        "slot",
                        0
                    )
                )

                if number > 7:
                    continue


                state = item.get(
                    "status",
                    "UNKNOWN"
                )


                with st.container(
                    border=True
                ):

                    a, b = st.columns(
                        [2, 1]
                    )


                    with a:

                        st.write(
                            f"### 🚘 Slot {number}"
                        )


                    with b:

                        if number in reservations:

                            st.warning(
                                "🟡 RESERVED"
                            )

                        elif state == "EMPTY":

                            st.success(
                                "🟢 AVAILABLE"
                            )

                        elif state == "FULL":

                            st.error(
                                "🔴 OCCUPIED"
                            )

                        else:

                            st.warning(
                                "UNKNOWN"
                            )


        with right_col:

            st.subheader(
                "➡️ Right Parking Row"
            )


            for item in slots:

                number = int(
                    item.get(
                        "slot",
                        0
                    )
                )

                if number <= 7:
                    continue


                state = item.get(
                    "status",
                    "UNKNOWN"
                )


                with st.container(
                    border=True
                ):

                    a, b = st.columns(
                        [2, 1]
                    )


                    with a:

                        st.write(
                            f"### 🚘 Slot {number}"
                        )


                    with b:

                        if number in reservations:

                            st.warning(
                                "🟡 RESERVED"
                            )

                        elif state == "EMPTY":

                            st.success(
                                "🟢 AVAILABLE"
                            )

                        elif state == "FULL":

                            st.error(
                                "🔴 OCCUPIED"
                            )

                        else:

                            st.warning(
                                "UNKNOWN"
                            )


# ============================================================
# RESERVATIONS
# ============================================================

elif page == "🎫 Reservations":

    st.header(
        "🎫 Parking Slot Reservation"
    )

    st.caption(
        "Reserve available parking spaces for incoming vehicles"
    )

    st.divider()


    status = read_status()

    slots = status.get(
        "slots",
        []
    )

    reservations = (
        get_active_reservations()
    )


    available_slots = []


    for item in slots:

        number = int(
            item.get(
                "slot",
                0
            )
        )

        state = item.get(
            "status"
        )

        if (
            state == "EMPTY"
            and
            number not in reservations
        ):

            available_slots.append(
                number
            )


    left, right = st.columns(
        [1, 1]
    )


    # --------------------------------------------------------
    # CREATE RESERVATION
    # --------------------------------------------------------

    with left:

        with st.container(
            border=True
        ):

            st.subheader(
                "➕ New Reservation"
            )


            if not available_slots:

                st.warning(
                    "No free unreserved slots "
                    "are currently available."
                )

            else:

                selected_slot = st.selectbox(
                    "Select parking slot",
                    available_slots
                )


                customer_name = st.text_input(
                    "Customer / Driver Name"
                )


                vehicle_number = st.text_input(
                    "Vehicle Registration Number",
                    placeholder="TN 01 AB 1234"
                )


                if st.button(
                    "🎫 Reserve Slot",
                    type="primary",
                    use_container_width=True
                ):

                    if not vehicle_number.strip():

                        st.error(
                            "Please enter the "
                            "vehicle registration number."
                        )

                    else:

                        success, message = (
                            create_reservation(
                                selected_slot,
                                customer_name,
                                vehicle_number
                            )
                        )


                        if success:

                            st.success(
                                message
                            )

                            time.sleep(
                                0.5
                            )

                            st.rerun()

                        else:

                            st.error(
                                message
                            )


    # --------------------------------------------------------
    # ACTIVE RESERVATIONS
    # --------------------------------------------------------

    with right:

        with st.container(
            border=True
        ):

            st.subheader(
                "🟡 Active Reservations"
            )


            active = (
                get_active_reservations()
            )


            if not active:

                st.info(
                    "No active reservations."
                )

            else:

                for slot, data in (
                    active.items()
                ):

                    st.write(
                        f"### Slot {slot}"
                    )

                    st.write(
                        f"🚘 **Vehicle:** "
                        f"{data['vehicle_number']}"
                    )

                    if data[
                        "customer_name"
                    ]:

                        st.write(
                            f"👤 **Driver:** "
                            f"{data['customer_name']}"
                        )

                    st.caption(
                        f"Reserved at "
                        f"{data['reserved_at']}"
                    )


                    if st.button(
                        f"Cancel Slot {slot}",
                        key=f"cancel_{data['id']}"
                    ):

                        cancel_reservation(
                            data["id"]
                        )

                        st.success(
                            f"Reservation for "
                            f"Slot {slot} cancelled."
                        )

                        time.sleep(
                            0.5
                        )

                        st.rerun()


                    st.divider()


# ============================================================
# ANALYTICS
# ============================================================

elif page == "📊 Analytics":

    st.header(
        "📊 Smart Parking Analytics"
    )

    st.caption(
        "Historical occupancy and utilization analysis"
    )

    st.divider()


    history = get_history()


    if history.empty:

        st.info(
            "No parking history has been recorded yet."
        )

        st.write(
            "Start AI Detection and allow the "
            "system to run for a short period."
        )

    else:

        latest = history.iloc[
            -1
        ]


        average_occupancy = (
            history[
                "occupancy"
            ].mean()
        )


        peak_row = history.loc[
            history[
                "occupied"
            ].idxmax()
        ]


        peak_occupied = int(
            peak_row[
                "occupied"
            ]
        )


        peak_time = peak_row[
            "timestamp"
        ]


        average_free = (
            history[
                "free"
            ].mean()
        )


        c1, c2, c3, c4 = (
            st.columns(4)
        )


        c1.metric(
            "Average Occupancy",
            f"{average_occupancy:.1f}%"
        )


        c2.metric(
            "Peak Occupied Slots",
            peak_occupied
        )


        c3.metric(
            "Average Free Slots",
            f"{average_free:.1f}"
        )


        c4.metric(
            "Recorded Samples",
            len(history)
        )


        st.caption(
            f"Peak occupancy recorded at: "
            f"{peak_time}"
        )


        st.divider()


        st.subheader(
            "📈 Occupancy Over Time"
        )


        chart_history = (
            history[
                [
                    "timestamp",
                    "occupied",
                    "free",
                    "reserved",
                ]
            ]
            .set_index(
                "timestamp"
            )
        )


        st.line_chart(
            chart_history
        )


        st.subheader(
            "📉 Occupancy Percentage"
        )


        occupancy_chart = (
            history[
                [
                    "timestamp",
                    "occupancy",
                    "utilization",
                ]
            ]
            .set_index(
                "timestamp"
            )
        )


        st.line_chart(
            occupancy_chart
        )


        st.divider()


        st.subheader(
            "🗂 Recent Parking History"
        )


        st.dataframe(
            history.tail(
                50
            ).sort_values(
                "timestamp",
                ascending=False
            ),
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# RESULTS
# ============================================================

elif page == "📈 Results":

    st.header(
        "📈 Detection Results & Reports"
    )

    st.divider()


    status = read_status()

    effective = (
        calculate_effective_status(
            status
        )
    )


    c1, c2, c3, c4 = (
        st.columns(4)
    )


    c1.metric(
        "Total",
        effective["total"]
    )

    c2.metric(
        "Free",
        effective["free"]
    )

    c3.metric(
        "Reserved",
        effective["reserved"]
    )

    c4.metric(
        "Occupied",
        effective["occupied"]
    )


    st.divider()


    # --------------------------------------------------------
    # VIDEO
    # --------------------------------------------------------

    st.subheader(
        "🎥 Processed AI Parking Video"
    )


    if detector_is_live(
        status
    ):

        st.info(
            "Detection is currently running."
        )

        st.warning(
            "Press Q inside the OpenCV window "
            "before viewing the final video."
        )


    elif WEB_VIDEO.exists():

        video_bytes = (
            WEB_VIDEO.read_bytes()
        )


        st.video(
            video_bytes,
            format="video/mp4"
        )


        st.download_button(
            "⬇️ Download Processed Video",
            data=video_bytes,
            file_name="SmartPark_AI_Result.mp4",
            mime="video/mp4"
        )


    elif RAW_VIDEO.exists():

        st.warning(
            "Raw detection output exists, "
            "but the web-compatible version "
            "has not yet been generated."
        )

    else:

        st.info(
            "Run AI Detection first."
        )


    st.divider()


    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    st.subheader(
        "📋 Current Parking Report"
    )


    report = (
        generate_current_report()
    )


    if report.empty:

        st.info(
            "No slot data available."
        )

    else:

        st.dataframe(
            report,
            hide_index=True,
            use_container_width=True
        )


        csv_data = report.to_csv(
            index=False
        ).encode(
            "utf-8"
        )


        st.download_button(
            "⬇️ Download Current Parking Report",
            data=csv_data,
            file_name=(
                "SmartPark_Current_Report.csv"
            ),
            mime="text/csv"
        )


    history = get_history()


    if not history.empty:

        history_csv = history.to_csv(
            index=False
        ).encode(
            "utf-8"
        )


        st.download_button(
            "⬇️ Download Parking History",
            data=history_csv,
            file_name="SmartPark_History.csv",
            mime="text/csv"
        )


# ============================================================
# SYSTEM PAGE
# ============================================================

elif page == "⚙️ System":

    st.header(
        "⚙️ SmartPark System Management"
    )

    st.divider()


    status = read_status()


    left, right = st.columns(
        2
    )


    with left:

        with st.container(
            border=True
        ):

            st.subheader(
                "🤖 AI Engine"
            )


            if detector_is_live(
                status
            ):

                st.success(
                    "AI Detector ONLINE"
                )

            else:

                st.warning(
                    "AI Detector OFFLINE"
                )


            st.write(
                "**Model:** Support Vector Machine"
            )

            st.write(
                "**Parking Slots:** 14"
            )

            st.write(
                "**Input:** Parking Lot Video"
            )

            st.write(
                "**Detection:** EMPTY / OCCUPIED"
            )


    with right:

        with st.container(
            border=True
        ):

            st.subheader(
                "💾 Data Storage"
            )

            st.write(
                f"Database: `{DATABASE_FILE.name}`"
            )

            history = get_history()

            st.metric(
                "History Records",
                len(history)
            )

            active_count = len(
                get_active_reservations()
            )

            st.metric(
                "Active Reservations",
                active_count
            )


    st.divider()


    st.subheader(
        "🧹 Maintenance"
    )


    if st.button(
        "Clear Parking History"
    ):

        with get_connection() as conn:

            conn.execute(
                "DELETE FROM parking_history"
            )

            conn.commit()


        st.success(
            "Parking history cleared."
        )

        time.sleep(
            0.5
        )

        st.rerun()


# ============================================================
# ABOUT
# ============================================================

elif page == "ℹ️ About Project":

    st.header(
        "ℹ️ About SmartPark AI"
    )

    st.divider()


    col1, col2 = st.columns(
        [1.5, 1]
    )


    with col1:

        with st.container(
            border=True
        ):

            st.subheader(
                "🎓 Project Title"
            )

            st.write(
                "**AI-Based Smart Parking Slot "
                "Detection and Management System**"
            )


        with st.container(
            border=True
        ):

            st.subheader(
                "🎯 Objective"
            )

            st.write(
                """
                SmartPark AI automatically identifies
                vacant and occupied parking spaces using
                computer vision and machine learning.

                The enhanced application adds reservations,
                occupancy analytics, historical tracking and
                downloadable parking reports.
                """
            )


        with st.container(
            border=True
        ):

            st.subheader(
                "✨ Enhanced Features"
            )

            st.write(
                "✅ AI parking detection"
            )

            st.write(
                "✅ 14-slot live parking map"
            )

            st.write(
                "✅ Parking reservations"
            )

            st.write(
                "✅ Vehicle registration tracking"
            )

            st.write(
                "✅ Historical occupancy analytics"
            )

            st.write(
                "✅ Peak-time analysis"
            )

            st.write(
                "✅ CSV report generation"
            )

            st.write(
                "✅ Processed detection video"
            )


    with col2:

        with st.container(
            border=True
        ):

            st.subheader(
                "🧰 Technology Stack"
            )

            st.write(
                "🐍 Python"
            )

            st.write(
                "👁 OpenCV"
            )

            st.write(
                "🧠 Scikit-learn"
            )

            st.write(
                "🤖 Support Vector Machine"
            )

            st.write(
                "📊 Streamlit"
            )

            st.write(
                "🗃 SQLite"
            )

            st.write(
                "🐼 Pandas"
            )


    st.divider()


    st.subheader(
        "🏗 Enhanced System Architecture"
    )


    st.code(
        """
Parking Camera / Video
          ↓
OpenCV Frame Processing
          ↓
14 Parking Slot ROIs
          ↓
SVM Classification
          ↓
EMPTY / OCCUPIED
          ↓
parking_status.json
          ↓
SmartPark Application
          │
          ├── Live Dashboard
          ├── Slot Reservation
          ├── Vehicle Information
          ├── SQLite History
          ├── Occupancy Analytics
          ├── Peak-Time Analysis
          ├── CSV Reports
          └── Processed Video
        """
    )


    st.success(
        "🚗 SmartPark AI — Enhanced Prototype v5.0"
    )
