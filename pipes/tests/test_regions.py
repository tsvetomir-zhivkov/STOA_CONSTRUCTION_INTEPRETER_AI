import cv2
import numpy as np

from pipes.raster import regions as rg


def canvas():
    return np.zeros((600, 900), np.uint8)


def rect(img, x0, y0, x1, y1, gap=None):
    cv2.rectangle(img, (x0, y0), (x1, y1), 1, 3)
    if gap:  # text interrupts the top side
        img[y0 - 3 : y0 + 4, gap[0] : gap[1]] = 0


def test_frame_with_text_gap_is_found_and_adjacent_frames_stay_separate():
    img = canvas()
    rect(img, 50, 50, 400, 250, gap=(150, 300))
    rect(img, 500, 50, 850, 250)
    text = np.zeros(img.shape, bool)
    text[40:60, 150:300] = True
    frames = rg.find_frames(img.astype(bool), 3.0, text)
    assert len(frames) == 2
    assert {round(f[0] / 10) for f in frames} == {5, 50}


def test_gap_without_text_is_not_bridged():
    img = canvas()
    rect(img, 50, 50, 400, 250, gap=(150, 300))
    assert rg.find_frames(img.astype(bool), 3.0, None) == []


def test_pipe_loop_with_inline_symbol_is_not_a_frame():
    img = canvas()
    rect(img, 50, 50, 400, 250)
    frames = rg.find_frames(img.astype(bool), 3.0, None)
    assert len(frames) == 1
    assert rg.classify(img.astype(bool), 3.0, frames, [[200, 40, 220, 60]], []) == []  # valve on the top side
    out = rg.classify(img.astype(bool), 3.0, frames, [[200, 120, 220, 140]], [])  # symbol inside only
    assert [r["kind"] for r in out] == ["other"] and out[0]["erase"] == "outline"


def test_oversized_box_on_the_border_does_not_block_a_frame():
    img = canvas()
    rect(img, 50, 50, 400, 250)
    frames = rg.find_frames(img.astype(bool), 3.0, None)
    big = [[380, 20, 520, 280]]  # as tall as the frame: a false detection straddling its side
    assert [r["kind"] for r in rg.classify(img.astype(bool), 3.0, frames, big, [])] == ["other"]
