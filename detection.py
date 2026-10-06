import cv2
import json
import os
import pickle
from datetime import datetime
from pathlib import Path

from skimage.transform import resize


# ============================================================
# PATH CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
FILES_DIR = BASE_DIR / "files"

MODEL_PATH = BASE_DIR / "model.p"
STATUS_FILE = BASE_DIR / "parking_status.json"
OUTPUT_VIDEO_PATH = FILES_DIR / "parking_output.mp4"


# ============================================================
# FIND INPUT VIDEO
# ============================================================

preferred_video = FILES_DIR / "parking_crop.mp4"

if preferred_video.exists():
    VIDEO_PATH = preferred_video
else:
    videos = [
        p for p in FILES_DIR.glob("*.mp4")
        if p.name.lower() != "parking_output.mp4"
    ]

    if not videos:
        raise FileNotFoundError(
            "No input MP4 video found inside the files folder."
        )

    VIDEO_PATH = videos[0]


# ============================================================
# 14 PARKING SLOT COORDINATES
# Format: (x, y, width, height)
# ============================================================

PARKING_SLOTS = [

    # LEFT COLUMN
    (60, 5, 140, 50),
    (60, 60, 140, 55),
    (60, 120, 140, 55),
    (60, 180, 140, 55),
    (60, 240, 140, 55),
    (60, 300, 140, 55),
    (60, 360, 140, 55),

    # RIGHT COLUMN
    (220, 5, 140, 50),
    (220, 60, 140, 55),
    (220, 120, 140, 55),
    (220, 180, 140, 55),
    (220, 240, 140, 55),
    (220, 300, 140, 55),
    (220, 360, 140, 55),
]


TOTAL_SLOTS = len(PARKING_SLOTS)


# ============================================================
# WRITE STATUS FOR STREAMLIT
# ============================================================

def save_status(
    available,
    occupied,
    slots,
    running=True
):
    if TOTAL_SLOTS > 0:
        occupancy = round(
            (occupied / TOTAL_SLOTS) * 100,
            1
        )
    else:
        occupancy = 0.0

    data = {
        "running": running,
        "updated_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "total": TOTAL_SLOTS,
        "available": available,
        "occupied": occupied,
        "occupancy": occupancy,
        "slots": slots,
    }

    temp_file = STATUS_FILE.with_suffix(".tmp")

    try:
        temp_file.write_text(
            json.dumps(data, indent=4),
            encoding="utf-8"
        )

        os.replace(
            temp_file,
            STATUS_FILE
        )

    except Exception as error:
        print(
            "Unable to update parking_status.json:",
            error
        )


# ============================================================
# LOAD TRAINED SVM MODEL
# ============================================================

print("Loading trained SmartPark model...")

if not MODEL_PATH.exists():
    raise FileNotFoundError(
        f"model.p not found at {MODEL_PATH}"
    )

with open(MODEL_PATH, "rb") as file:
    model = pickle.load(file)

print("Model loaded successfully.")
print(f"Using video: {VIDEO_PATH}")
print(f"Total parking slots: {TOTAL_SLOTS}")


# ============================================================
# SLOT CLASSIFICATION FUNCTION
# ============================================================

def classify_slot(roi):

    # OpenCV is BGR, convert to RGB
    roi_rgb = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2RGB
    )

    # Resize exactly like SmartParkCV preprocessing
    resized = resize(
        roi_rgb,
        (15, 15, 3),
        anti_aliasing=True
    )

    # 15 x 15 x 3 = 675 features
    features = resized.flatten()

    prediction = model.predict(
        [features]
    )[0]

    return int(prediction)


# ============================================================
# OPEN INPUT VIDEO
# ============================================================

cap = cv2.VideoCapture(
    str(VIDEO_PATH)
)

if not cap.isOpened():
    raise RuntimeError(
        f"Unable to open video: {VIDEO_PATH}"
    )


fps = cap.get(
    cv2.CAP_PROP_FPS
)

if fps <= 0:
    fps = 25.0


frame_width = int(
    cap.get(cv2.CAP_PROP_FRAME_WIDTH)
)

frame_height = int(
    cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
)


# ============================================================
# OUTPUT VIDEO
# ============================================================

fourcc = cv2.VideoWriter_fourcc(
    *"mp4v"
)

writer = cv2.VideoWriter(
    str(OUTPUT_VIDEO_PATH),
    fourcc,
    fps,
    (
        frame_width,
        frame_height
    )
)


# ============================================================
# INITIAL STATUS
# ============================================================

save_status(
    available=0,
    occupied=0,
    slots=[],
    running=True
)


print("")
print("=" * 50)
print("SMARTPARK AI DETECTION STARTED")
print("=" * 50)
print("Press Q inside the detection window to stop.")
print("")


last_available = 0
last_occupied = 0
last_slots = []


# ============================================================
# MAIN VIDEO PROCESSING LOOP
# ============================================================

try:

    while True:

        success, frame = cap.read()

        if not success:
            print("Video processing completed.")
            break


        available_count = 0
        occupied_count = 0

        slot_status_list = []


        # ====================================================
        # PROCESS ALL 14 PARKING SPACES
        # ====================================================

        for slot_number, (
            x,
            y,
            width,
            height
        ) in enumerate(
            PARKING_SLOTS,
            start=1
        ):

            roi = frame[
                y:y + height,
                x:x + width
            ]


            if roi.size == 0:

                status = "ERROR"
                color = (0, 255, 255)

            else:

                try:

                    prediction = classify_slot(
                        roi
                    )


                    # ----------------------------------------
                    # CLASS 0 = EMPTY
                    # ----------------------------------------

                    if prediction == 0:

                        status = "EMPTY"

                        available_count += 1

                        # Green
                        color = (0, 255, 0)


                    # ----------------------------------------
                    # CLASS 1 = OCCUPIED
                    # ----------------------------------------

                    else:

                        status = "FULL"

                        occupied_count += 1

                        # Red
                        color = (0, 0, 255)


                except Exception as error:

                    print(
                        f"Slot {slot_number} error:",
                        error
                    )

                    status = "ERROR"
                    color = (0, 255, 255)


            # =================================================
            # DRAW PARKING RECTANGLE
            # =================================================

            cv2.rectangle(
                frame,
                (x, y),
                (
                    x + width,
                    y + height
                ),
                color,
                3
            )


            # =================================================
            # SLOT LABEL
            # =================================================

            label = (
                f"S{slot_number} {status}"
            )

            (
                text_width,
                text_height
            ), _ = cv2.getTextSize(
                label,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                2
            )


            cv2.rectangle(
                frame,
                (x, y),
                (
                    x + text_width + 10,
                    y + text_height + 12
                ),
                color,
                -1
            )


            cv2.putText(
                frame,
                label,
                (
                    x + 5,
                    y + text_height + 5
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (255, 255, 255),
                2
            )


            slot_status_list.append(
                {
                    "slot": slot_number,
                    "status": status
                }
            )


        # ====================================================
        # DASHBOARD STATUS UPDATE
        # ====================================================

        save_status(
            available=available_count,
            occupied=occupied_count,
            slots=slot_status_list,
            running=True
        )


        last_available = available_count
        last_occupied = occupied_count
        last_slots = slot_status_list


        # ====================================================
        # DISPLAY OVERALL STATISTICS
        # ====================================================

        occupancy_percentage = (
            occupied_count / TOTAL_SLOTS
        ) * 100


        summary = (
            f"Available: {available_count} | "
            f"Occupied: {occupied_count} | "
            f"Occupancy: {occupancy_percentage:.1f}%"
        )


        cv2.rectangle(
            frame,
            (5, frame_height - 40),
            (
                frame_width - 5,
                frame_height - 5
            ),
            (30, 30, 30),
            -1
        )


        cv2.putText(
            frame,
            summary,
            (
                10,
                frame_height - 15
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (255, 255, 255),
            1
        )


        # ====================================================
        # SAVE OUTPUT VIDEO
        # ====================================================

        writer.write(frame)


        # ====================================================
        # DISPLAY WINDOW
        # ====================================================

        cv2.imshow(
            "SmartPark AI - Parking Lot Detection",
            frame
        )


        delay = max(
            1,
            int(1000 / fps)
        )

        key = cv2.waitKey(delay)

        if key & 0xFF == ord("q"):
            print("Detection stopped by user.")
            break


# ============================================================
# CLEANUP
# ============================================================

finally:

    cap.release()
    writer.release()

    cv2.destroyAllWindows()


    save_status(
        available=last_available,
        occupied=last_occupied,
        slots=last_slots,
        running=False
    )


    print("")
    print("=" * 50)
    print("SMARTPARK DETECTION STOPPED")
    print("=" * 50)

    print(
        f"Available spaces: {last_available}"
    )

    print(
        f"Occupied spaces: {last_occupied}"
    )

    print(
        f"Output saved to: {OUTPUT_VIDEO_PATH}"
    )
