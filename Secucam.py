#!/usr/bin/env python3

import os
import subprocess
import datetime
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import cv2
from PIL import Image, ImageTk

# SETTINGS
CAMERA_RTSP_1 = "rtsp://admin:labh2708@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0"
CAMERA_RTSP_2 = "rtsp://admin:labh2708@192.168.1.107:554/cam/realmonitor?channel=1&subtype=0"
OUTPUT_DIR = "recordings"
ARCHIVE_DIR = "archive"
ENCODER = "h264"  # Adjust to your system. Use "h264_amf" on AMD, "h264_nvenc" on NVIDIA

# State Flags
recording_active = False
display_active = False
recording_procs = {}
video_threads = []
capture_objects = []

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# === RECORDING FUNCTION ===
def record_video():
    global recording_procs, recording_active

    now = datetime.datetime.now()
    start_time = now.replace(minute=0, second=0, microsecond=0)
    if now.hour < 12:
        start_time = start_time.replace(hour=0)
    else:
        start_time = start_time.replace(hour=12)

    filename1 = f"{OUTPUT_DIR}/camera1_{start_time.strftime('%Y-%m-%d_%H-%M')}.mp4"
    filename2 = f"{OUTPUT_DIR}/camera2_{start_time.strftime('%Y-%m-%d_%H-%M')}.mp4"

    command1 = [
        "ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", CAMERA_RTSP_1,
        "-c:v", ENCODER, "-b:v", "2M", "-preset", "fast", "-t", "43200", filename1
    ]
    command2 = [
        "ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", CAMERA_RTSP_2,
        "-c:v", ENCODER, "-b:v", "2M", "-preset", "fast", "-t", "43200", filename2
    ]

    try:
        recording_procs["cam1"] = subprocess.Popen(command1)
        recording_procs["cam2"] = subprocess.Popen(command2)
        recording_active = True
        update_status()
        recording_procs["cam1"].wait()
        recording_procs["cam2"].wait()
    except Exception as e:
        print("Recording error:", e)
    finally:
        recording_active = False
        update_status()


def start_recording():
    if not recording_active:
        threading.Thread(target=record_video, daemon=True).start()


# === DISPLAY FUNCTION ===
def toggle_video():
    global display_active
    if display_active:
        display_active = False
        update_status()
    else:
        display_active = True
        threading.Thread(target=display_cameras, daemon=True).start()


def display_cameras():
    global display_active
    cap1 = cv2.VideoCapture(CAMERA_RTSP_1)
    cap2 = cv2.VideoCapture(CAMERA_RTSP_2)

    while display_active:
        for cam_num, cap, panel in [(1, cap1, cam1_panel), (2, cap2, cam2_panel)]:
            ret, frame = cap.read()
            if ret:
                frame = cv2.resize(frame, (300, 200))
                image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = ImageTk.PhotoImage(Image.fromarray(image))
                panel.configure(image=img)
                panel.image = img
        root.update()
    cap1.release()
    cap2.release()
    cam1_panel.config(image="")
    cam2_panel.config(image="")


# === FILE MANAGEMENT ===
def archive_file(file):
    os.rename(f"{OUTPUT_DIR}/{file}", f"{ARCHIVE_DIR}/{file}")
    refresh_file_list()


def delete_old_files():
    now = datetime.datetime.now()
    for file in os.listdir(OUTPUT_DIR):
        file_path = os.path.join(OUTPUT_DIR, file)
        if os.path.isfile(file_path):
            try:
                file_time = datetime.datetime.strptime(file.split("_")[1], "%Y-%m-%d_%H-%M.mp4")
                if (now - file_time).days > 7:
                    os.remove(file_path)
            except:
                pass


def refresh_file_list():
    file_list.delete(0, tk.END)
    for file in sorted(os.listdir(OUTPUT_DIR)):
        file_list.insert(tk.END, file)


def on_file_select(event):
    selected = file_list.curselection()
    if selected:
        file_name = file_list.get(selected[0])
        action_menu["state"] = "normal"
        action_menu["text"] = f"Manage: {file_name}"
        action_menu["menu"].delete(0, "end")
        action_menu["menu"].add_command(label="Archive", command=lambda: archive_file(file_name))
        action_menu["menu"].add_command(label="Delete", command=lambda: os.remove(f"{OUTPUT_DIR}/{file_name}") or refresh_file_list())
    else:
        action_menu["state"] = "disabled"


def update_status():
    status = f"Display: {'ON' if display_active else 'OFF'} | Recording: {'ON' if recording_active else 'OFF'}"
    status_label.config(text=status)


# === GUI SETUP ===
root = tk.Tk()
root.title("Security Footage Manager")
root.geometry("800x600")

frame = tk.Frame(root)
frame.pack(pady=10)

file_list = tk.Listbox(frame, width=50, height=15)
file_list.pack(side=tk.LEFT, fill=tk.BOTH)
file_list.bind("<<ListboxSelect>>", on_file_select)

scrollbar = tk.Scrollbar(frame, orient="vertical")
scrollbar.config(command=file_list.yview)
scrollbar.pack(side=tk.RIGHT, fill="y")
file_list.config(yscrollcommand=scrollbar.set)

action_menu = ttk.Menubutton(root, text="Select File", state="disabled", direction="below")
action_menu.pack(pady=10)

# Video Display Panel
cam_frame = tk.Frame(root)
cam_frame.pack(pady=5)

cam1_panel = tk.Label(cam_frame)
cam1_panel.pack(side=tk.LEFT, padx=10)

cam2_panel = tk.Label(cam_frame)
cam2_panel.pack(side=tk.RIGHT, padx=10)

# Buttons
btn_frame = tk.Frame(root)
btn_frame.pack(pady=10)

record_btn = tk.Button(btn_frame, text="Start Recording", command=start_recording)
record_btn.grid(row=0, column=0, padx=5)

video_btn = tk.Button(btn_frame, text="Toggle Live Display", command=toggle_video)
video_btn.grid(row=0, column=1, padx=5)

status_label = tk.Label(root, text="Display: OFF | Recording: OFF", font=("Arial", 10))
status_label.pack(pady=5)

refresh_file_list()
root.mainloop()
