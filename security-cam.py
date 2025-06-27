import cv2
import os
import tkinter as tk
from tkinter import messagebox
from tkinter import filedialog
import threading
import datetime
import time
import subprocess

# Configuration
CAMERA_1_INDEX = 0
CAMERA_2_INDEX = 1
RECORD_INTERVAL_HOURS = 12
OUTPUT_DIR = os.path.join(os.getcwd(), "recordings")
ARCHIVE_DIR = os.path.join(os.getcwd(), "archived")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

recording = False
camera_feeds = {1: None, 2: None}
recording_status = None
feed_threads = {}

# Open folder

def open_folder():
    try:
        os.startfile(OUTPUT_DIR)
    except Exception as e:
        messagebox.showerror("Error", str(e))

# Check camera availability

def check_camera(index):
    cap = cv2.VideoCapture(index)
    if cap is None or not cap.isOpened():
        return False
    cap.release()
    return True

def update_status():
    cam1 = "Online" if check_camera(CAMERA_1_INDEX) else "Offline"
    cam2 = "Online" if check_camera(CAMERA_2_INDEX) else "Offline"
    cam1_status.config(text=f"Camera 1: {cam1}")
    cam2_status.config(text=f"Camera 2: {cam2}")

# Recording logic

def get_output_path(camera_id):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"camera{camera_id}_{timestamp}.mp4"
    return os.path.join(OUTPUT_DIR, filename)

def record_from_camera(index, camera_id):
    global recording
    cap = cv2.VideoCapture(index)
    fourcc = cv2.VideoWriter_fourcc(*'X264')  # H.264 encoding
    output_path = get_output_path(camera_id)
    out = None

    if not cap.isOpened():
        print(f"Camera {camera_id} failed to open.")
        return

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = 24

    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    start_time = time.time()
    max_duration = RECORD_INTERVAL_HOURS * 3600

    while time.time() - start_time < max_duration and recording:
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)

    cap.release()
    if out:
        out.release()
    print(f"Camera {camera_id} stopped recording.")

# Scheduler

def schedule_recording():
    global recording
    while True:
        now = datetime.datetime.now()
        if now.hour in [0, 12] and now.minute == 0:
            if not recording:
                recording = True
                threading.Thread(target=record_all).start()
        time.sleep(60)  # check every minute

# Manage multiple cameras

def record_all():
    global recording
    threading.Thread(target=record_from_camera, args=(CAMERA_1_INDEX, 1)).start()
    threading.Thread(target=record_from_camera, args=(CAMERA_2_INDEX, 2)).start()
    duration = RECORD_INTERVAL_HOURS * 3600
    time.sleep(duration)
    recording = False
    print("Recording cycle completed.")

# Show feed

def show_feed(camera_id):
    index = CAMERA_1_INDEX if camera_id == 1 else CAMERA_2_INDEX

    if camera_feeds[camera_id] is not None:
        camera_feeds[camera_id] = None
        return

    def run():
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            messagebox.showerror("Error", f"Camera {camera_id} not available.")
            return

        camera_feeds[camera_id] = True
        window_name = f"Camera {camera_id} Feed"

        while camera_feeds[camera_id]:
            ret, frame = cap.read()
            if not ret:
                break
            resized = cv2.resize(frame, (640, 480))
            cv2.imshow(window_name, resized)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        camera_feeds[camera_id] = None
        cap.release()
        cv2.destroyWindow(window_name)

    threading.Thread(target=run).start()

# GUI

root = tk.Tk()
root.title("Security Camera System")
root.geometry("400x300")

btn1 = tk.Button(root, text="Show Camera 1 Feed", command=lambda: show_feed(1))
btn1.pack(pady=5)

btn2 = tk.Button(root, text="Show Camera 2 Feed", command=lambda: show_feed(2))
btn2.pack(pady=5)

cam1_status = tk.Label(root, text="Camera 1: Checking...")
cam1_status.pack(pady=2)

cam2_status = tk.Label(root, text="Camera 2: Checking...")
cam2_status.pack(pady=2)

recording_status = tk.Label(root, text="Recording: No")
recording_status.pack(pady=5)

def update_gui():
    update_status()
    recording_status.config(text=f"Recording: {'Yes' if recording else 'No'}")
    root.after(5000, update_gui)  # Update every 5 seconds

btn_open = tk.Button(root, text="📁 Open Folder", command=open_folder)
btn_open.pack(pady=10)

update_gui()

# Start scheduler thread
threading.Thread(target=schedule_recording, daemon=True).start()

root.mainloop()
