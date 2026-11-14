"""Frozen detector settings for the pipe route (decided 2026-11-14 after the 20-sheet comparison).

full = trained on all 65 train sheets + offline augmentation; tiles (640 px) + whole-sheet pass (imgsz 1280);
boxes >= CONF_REPORT are reported symbols, boxes >= CONF_ERASE are erased before tracing (not reported).
"""

DETECTOR_RUN = "full"
DETECTOR_PASSES = ("tile", "whole")
CONF_REPORT = 0.5
CONF_ERASE = 0.25
