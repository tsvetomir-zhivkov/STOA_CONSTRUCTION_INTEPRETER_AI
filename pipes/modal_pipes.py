"""All GPU / Modal code for pipes (Modal app stoa-pipes). Step 1: app, volume and image; functions come later.

Run: python -m modal run -m pipes.modal_pipes::fetch_pid_dataset
"""

from __future__ import annotations

import modal

APP_NAME = "stoa-pipes"
app = modal.App(APP_NAME)
vol = modal.Volume.from_name("stoa-pipes-data", create_if_missing=True)
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0", "wget", "unzip")
    .pip_install("ultralytics==8.3.*", "sahi", "opencv-python-headless")
    .add_local_python_source("pipes")
)

PID_URL = "https://zenodo.org/records/8028570/files/PID_Dataset.zip?download=1"
PID_MD5 = "ade6a91d6299e3751486bb1422a658e0"


@app.function(image=image, volumes={"/data": vol}, timeout=3600)
def fetch_pid_dataset():
    """Step 2: download PID_Dataset.zip (6.7 GB, CC BY 4.0) into the volume, verify md5, unzip."""
    import hashlib
    import os
    import subprocess

    os.makedirs("/data/raw", exist_ok=True)
    subprocess.run(["wget", "-q", "-O", "/data/raw/PID_Dataset.zip", PID_URL], check=True)
    h = hashlib.md5()
    with open("/data/raw/PID_Dataset.zip", "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != PID_MD5:
        raise RuntimeError(f"md5 mismatch: {h.hexdigest()} != {PID_MD5}")
    subprocess.run(
        ["unzip", "-q", "-o", "-d", "/data/raw/pid_dataset", "/data/raw/PID_Dataset.zip"], check=True
    )
    vol.commit()
