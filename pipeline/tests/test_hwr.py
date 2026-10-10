"""Handwriting trial: region detection by ink colour, and scoring."""
import cv2
import numpy as np

from hwr.regions import find_regions, ink_masks
from hwr.trial import cer, score
from models import OCRResult, Word

BLUE_PEN = (200, 60, 20)     # BGR
RED_TAB = (30, 30, 220)
BLACK = (20, 20, 20)


def page(tmp_path):
    img = np.full((1400, 1000, 3), 250, np.uint8)
    cv2.rectangle(img, (100, 200), (500, 230), BLACK, -1)                     # printed text
    cv2.putText(img, "6.00", (420, 640), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 2, BLUE_PEN, 4)   # handwriting
    cv2.rectangle(img, (975, 0), (1000, 1400), RED_TAB, -1)                   # binder index tab at the edge
    cv2.rectangle(img, (300, 1000), (500, 1080), BLUE_PEN, 6)                 # PAID stamp
    cv2.putText(img, "RAND DAIRY", (150, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.5, RED_TAB, 5)  # letterhead printed in red
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), img)
    words = [Word("PRINTED", 100, 200, 400, 30, 99.0), Word("愛PAID", 300, 1000, 200, 80, 96.0),
             Word("RAND", 150, 80, 125, 45, 99.5), Word("DAIRY", 300, 80, 150, 45, 99.5)]
    return path, OCRResult(str(path), "t", "0", 0.0, 1000, 1400, words)


def test_blue_pen_is_ink_and_black_print_is_not():
    img = np.full((50, 100, 3), 250, np.uint8)
    img[10:20, 10:40] = BLUE_PEN
    img[30:40, 10:40] = BLACK
    masks = ink_masks(img)
    assert masks["blue"][15, 20] and not masks["blue"][35, 20] and not masks["red"].any()


def test_only_the_handwriting_survives_tabs_and_stamps(tmp_path):
    path, ocr = page(tmp_path)
    regions = find_regions(path, ocr)
    assert len(regions) == 1
    x0, _y0, _x1, y1 = regions[0].box
    assert regions[0].ink == "blue" and 380 < x0 < 520 and 560 < y1 < 700


def test_character_error_rate():
    assert cer("6.00", "6.00") == 0.0
    assert cer("600", "6.00") == 0.25
    assert cer("", "EFT") == 1.0


def test_trial_scores_detection_and_reading_separately():
    regions = [
        {"region_id": "a", "page": "p1", "paddle_text": "6.00"},
        {"region_id": "b", "page": "p1", "paddle_text": ""},
        {"region_id": "c", "page": "p2", "paddle_text": "POSTED"},
    ]
    trocr = {"a": {"text": "6 00"}, "b": {"text": "v"}, "c": {"text": "POSTED"}}
    labels = {
        "a": {"is_handwriting": True, "kind": "quantity", "legible": True, "text": "6.00"},
        "b": {"is_handwriting": True, "kind": "tick", "legible": True, "text": ""},
        "c": {"is_handwriting": False, "kind": "other", "legible": True, "text": "POSTED"},
    }
    s = score(regions, trocr, labels)
    assert s["detection_precision"] == round(2 / 3, 3)
    assert s["legible_text_regions_scored"] == 1 and s["handwriting_kinds"] == {"quantity": 1, "tick": 1}
    assert s["engines"]["paddle"]["all"]["exact_match_rate"] == 1.0
    assert s["engines"]["trocr"]["all"]["mean_cer"] == 0.25
