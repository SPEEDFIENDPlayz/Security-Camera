import cv2
import os
import time
import threading
from datetime import datetime, timedelta
from tkinter import *
from tkinter import ttk, messagebox

# === CONFIG ===
CAMERA_1_INDEX = "rtsp://admin:labh2708@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0"
CAMERA_2_INDEX = "rtsp://admin:labh2708@192.168.1.107:554/cam/realmonitor?channel=1&subtype=0"
OUTPUT_DIR = "/mnt/data/security_footage"
RECORD_INTERVAL_HOURS = 12
DELETE_AFTER_DAYS = 7
ARCHIVE_DIR = os.path.join(OUTPUT_DIR, "archived")

# === GLOBALS ===
recording = False
feed_windows = {}
camera_status = {1: "Unknown", 2: "Unknown"}

# === SETUP ===
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# === CAMERA FEED HANDLER ===
def check_camera_status(cam_index, label):
    cap = cv2.VideoCapture(cam_index)
    if cap.isOpened():
        ret, _ = cap.read()
        if ret:
            camera_status[cam_index + 1] = "Online"
            label.config(text="Camera {}: Online".format(cam_index + 1), fg="green")
        else:
            camera_status[cam_index + 1] = "Offline"
            label.config(text="Camera {}: Offline".format(cam_index + 1), fg="red")
    else:
        camera_status[cam_index + 1] = "Offline"
        label.config(text="Camera {}: Offline".format(cam_index + 1), fg="red")
    cap.release()


def toggle_feed(cam_index, title):
    if cam_index in feed_windows:
        cv2.destroyWindow(title)
        del feed_windows[cam_index]
        return

    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        messagebox.showerror("Error", f"Camera {cam_index} not available")
        return

    def show_feed():
        while cam_index in feed_windows:
            ret, frame = cap.read()
            if not ret:
                break
            cv2.imshow(title, frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        cap.release()
        cv2.destroyWindow(title)
        if cam_index in feed_windows:
            del feed_windows[cam_index]

    feed_windows[cam_index] = True
    threading.Thread(target=show_feed).start()


# === RECORDING FUNCTIONALITY ===
def record_camera(cam_index):
    global recording
    recording = True
    while recording:
        start_time = datetime.now()
        end_time = start_time + timedelta(hours=RECORD_INTERVAL_HOURS)
        filename = f"camera_{cam_index + 1}_{start_time.strftime('%Y-%m-%d_%H-%M')}.mp4"
        out_path = os.path.join(OUTPUT_DIR, filename)

        cap = cv2.VideoCapture(cam_index)
        fourcc = cv2.VideoWriter_fourcc(*'H264')
        out = cv2.VideoWriter(out_path, fourcc, 24.0, (1280, 720))

        while datetime.now() < end_time and recording:
            ret, frame = cap.read()
            if not ret:
                break
            out.write(frame)
        out.release()
        cap.release()

        delete_old_files()


def delete_old_files():
    for file in os.listdir(OUTPUT_DIR):
        full_path = os.path.join(OUTPUT_DIR, file)
        if os.path.isfile(full_path):
            ctime = os.path.getctime(full_path)
            if time.time() - ctime > DELETE_AFTER_DAYS * 86400:
                os.remove(full_path)


def start_recording():
    threading.Thread(target=record_camera, args=(CAMERA_1_INDEX,), daemon=True).start()
    threading.Thread(target=record_camera, args=(CAMERA_2_INDEX,), daemon=True).start()
    status_lbl.config(text="Recording: ON", fg="green")


def stop_recording():
    global recording
    recording = False
    status_lbl.config(text="Recording: OFF", fg="red")


# === GUI ===
root = Tk()
root.title("Security Camera System")
root.geometry("400x300")

btn_cam1 = Button(root, text="Show Camera Feed 1", command=lambda: toggle_feed(CAMERA_1_INDEX, "Camera 1"))
btn_cam1.pack(pady=5)

label_cam1 = Label(root, text="Camera 1: Checking...", fg="gray")
label_cam1.pack()

btn_cam2 = Button(root, text="Show Camera Feed 2", command=lambda: toggle_feed(CAMERA_2_INDEX, "Camera 2"))
btn_cam2.pack(pady=5)

label_cam2 = Label(root, text="Camera 2: Checking...", fg="gray")
label_cam2.pack()

status_lbl = Label(root, text="Recording: OFF", fg="red")
status_lbl.pack(pady=10)

btn_start = Button(root, text="Start Recording", command=start_recording)
btn_start.pack(pady=5)

btn_stop = Button(root, text="Stop Recording", command=stop_recording)
btn_stop.pack(pady=5)

# Check camera status on launch
threading.Thread(target=lambda: check_camera_status(CAMERA_1_INDEX, label_cam1), daemon=True).start()
threading.Thread(target=lambda: check_camera_status(CAMERA_2_INDEX, label_cam2), daemon=True).start()

root.mainloop()
