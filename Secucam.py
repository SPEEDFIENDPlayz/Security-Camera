#!/usr/bin/env python3

import os
import subprocess
import datetime
import threading
import tkinter as tk
from tkinter import messagebox, ttk

# SETTINGS
CAMERA_RTSP_1 = "rtsp://admin:labh2708@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0"
CAMERA_RTSP_2 = "rtsp://admin:labh2708@192.168.1.107:554/cam/realmonitor?channel=1&subtype=0"
OUTPUT_DIR = "/mnt/security_footage"
ARCHIVE_DIR = "/mnt/security_archived"
ENCODER = "h264_amf"  # Use "hevc_amf" for H.265

# Ensure directories exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)


# Recording function
def record_video():
    while True:
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

        p1 = subprocess.Popen(command1)
        p2 = subprocess.Popen(command2)

        p1.wait()
        p2.wait()


# Archive footage manually
def archive_file(file):
    os.rename(f"{OUTPUT_DIR}/{file}", f"{ARCHIVE_DIR}/{file}")
    refresh_file_list()


# Delete old recordings (7 days limit)
def delete_old_files():
    now = datetime.datetime.now()
    for file in os.listdir(OUTPUT_DIR):
        file_path = os.path.join(OUTPUT_DIR, file)
        if os.path.isfile(file_path):
            file_time = datetime.datetime.strptime(file.split("_")[1], "%Y-%m-%d_%H-%M.mp4")
            if (now - file_time).days > 7:
                os.remove(file_path)


# GUI for File Management
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
        action_menu["menu"].add_command(label="Delete", command=lambda: os.remove(f"{OUTPUT_DIR}/{file_name}"))
    else:
        action_menu["state"] = "disabled"


# Start recording in background
threading.Thread(target=record_video, daemon=True).start()

# Create GUI
root = tk.Tk()
root.title("Security Footage Manager")
root.geometry("600x400")

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

refresh_file_list()
root.mainloop()
