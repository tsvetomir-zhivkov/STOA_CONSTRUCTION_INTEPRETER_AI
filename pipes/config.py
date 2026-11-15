"""Frozen detector settings for the pipe route (decided 2026-11-14 after the 20-sheet comparison).

full = trained on all 65 train sheets + offline augmentation; tiles (640 px) + whole-sheet pass (imgsz 1280);
boxes >= CONF_REPORT are reported symbols, boxes >= CONF_ERASE are erased before tracing (not reported).
"""

DETECTOR_RUN = "full"
DETECTOR_PASSES = ("tile", "whole")
CONF_REPORT = 0.5
CONF_ERASE = 0.25

# Measured on the 20 dataset test sheets (1,180 symbols): recall of the frozen detector (tiles + whole sheet, conf >= 0.5)
# is 86.8%, precision 90.1%. Tools quote it, because coverage confidence reflects detector scores, not recall.
MEASURED_RECALL = 0.87
