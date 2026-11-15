import cv2
import numpy as np

from pipes.raster.equipment import find_equipment


def canvas():
    return np.zeros((600, 900), np.uint8)


def test_ellipse_with_an_attached_pipe_is_equipment():
    img = canvas()
    cv2.ellipse(img, (300, 300), (130, 90), 0, 0, 360, 1, 4)
    cv2.line(img, (430, 300), (800, 300), 1, 4)  # pipe joins the right side
    found = find_equipment(img.astype(bool), 4.0, [])
    assert len(found) == 1
    x0, y0, x1, y1 = found[0]["bbox_px"]
    assert x0 < 175 and x1 > 425 and y0 < 215 and y1 > 385
    assert found[0]["origin"] == "closed_contour"


def test_ellipse_without_a_pipe_is_not_equipment():
    img = canvas()
    cv2.ellipse(img, (300, 300), (130, 90), 0, 0, 360, 1, 4)
    assert find_equipment(img.astype(bool), 4.0, []) == []


def test_rectilinear_pipe_loop_is_not_equipment():
    img = canvas()
    cv2.rectangle(img, (100, 100), (500, 400), 1, 4)
    cv2.line(img, (500, 250), (850, 250), 1, 4)
    assert find_equipment(img.astype(bool), 4.0, []) == []


def test_already_boxed_equipment_is_skipped():
    img = canvas()
    cv2.ellipse(img, (300, 300), (130, 90), 0, 0, 360, 1, 4)
    cv2.line(img, (430, 300), (800, 300), 1, 4)
    assert find_equipment(img.astype(bool), 4.0, [[160, 200, 440, 400]]) == []


def test_stamp_in_the_page_margin_is_not_equipment():
    img = np.zeros((600, 1200), np.uint8)  # outer strip = 1104..1200
    cv2.circle(img, (1150, 300), 40, 1, 4)  # seal wholly inside the right-hand strip
    cv2.line(img, (900, 300), (1110, 300), 1, 4)  # a line reaches it
    assert find_equipment(img.astype(bool), 4.0, []) == []
    img2 = np.zeros((600, 1200), np.uint8)  # the same circle mid-page is equipment
    cv2.circle(img2, (600, 300), 40, 1, 4)
    cv2.line(img2, (640, 300), (900, 300), 1, 4)
    assert len(find_equipment(img2.astype(bool), 4.0, [])) == 1


def test_ink_dense_interior_is_not_equipment(monkeypatch):
    from pipes.raster import equipment

    img = canvas()
    cv2.circle(img, (300, 300), 110, 1, 4)
    cv2.rectangle(img, (270, 280), (330, 320), 1, -1)  # a solid block well inside, ~7% of the interior
    cv2.line(img, (410, 300), (800, 300), 1, 4)
    ink = img.astype(bool)
    assert len(find_equipment(ink, 4.0, [])) == 1  # at the default limit it is accepted
    monkeypatch.setattr(equipment, "INTERIOR_INK_MAX", 0.05)
    assert find_equipment(ink, 4.0, []) == []  # a stricter limit rejects it: the rule is what decides
