import json

import pytest

from models import OCRResult, Word
from ocr.tesseract import parse_tsv

TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t1760\t2720\t-1\t\n"
    "5\t1\t2\t1\t1\t1\t100\t200\t80\t20\t95.5\tTAX\n"
    "5\t1\t2\t1\t1\t2\t190\t200\t120\t20\t91.0\tINVOICE\n"
    "5\t1\t2\t1\t2\t1\t100\t230\t50\t20\t60.0\t   \n"
    "5\t1\t3\t1\t1\t1\t100\t400\t60\t20\t87.0\t211.69\n"
)


def test_parse_tsv_keeps_words_and_drops_blanks():
    words = parse_tsv(TSV)
    assert [w.text for w in words] == ["TAX", "INVOICE", "211.69"]
    assert words[0].conf == 95.5


def test_parse_tsv_offsets_crop_back_to_page_coordinates():
    words = parse_tsv(TSV, dx=10, dy=1000)
    assert (words[0].left, words[0].top) == (110, 1200)


def test_lines_are_unique_across_blocks():
    words = parse_tsv(TSV)
    assert words[0].line == words[1].line
    assert words[1].line != words[2].line


def test_cache_key_keeps_default_settings_and_separates_changed_ones(tmp_path):
    from ocr.cache import CachedOCR
    from ocr.paddle import PaddleEngine
    img = tmp_path / "p.jpg"
    img.write_bytes(b"not really an image")
    default = CachedOCR(PaddleEngine(), tmp_path)._key(img, None, 3).name
    capped = CachedOCR(PaddleEngine(text_det_limit_side_len=1280), tmp_path)._key(img, None, 3).name
    assert default.endswith("_full_psm3.json")        # same name as results cached before tags existed
    assert capped != default and capped.startswith(default[:-5])
    assert CachedOCR(PaddleEngine(text_det_limit_side_len=1280), tmp_path)._key(img, None, 3).name == capped


def test_thread_count_does_not_split_the_cache():
    from ocr.paddle import PaddleEngine
    assert PaddleEngine(cpu_threads=6).cache_tag() == ""


def test_batch_recognition_skips_pages_already_cached(tmp_path):
    from ocr.cache import CachedOCR
    from ocr.paddle import PaddleEngine
    from ocr.run_all import pages_to_do
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"page a")
    b.write_bytes(b"page b")
    ocr = CachedOCR(PaddleEngine(), tmp_path / "cache")
    result = OCRResult(str(a), "paddle", "test", 0, 100, 200, [])
    ocr._key(a, None, 3).write_text(json.dumps(result.to_dict()), encoding="utf-8")
    assert pages_to_do([a, b], ocr) == [b]


@pytest.mark.parametrize("broken", ["{", "{}", "null", '{"words": [null]}'])
def test_invalid_cache_is_recognised_again_and_replaced(tmp_path, broken):
    from ocr.cache import CachedOCR

    class Engine:
        name = "test"
        calls = 0

        def cache_tag(self):
            return ""

        def recognise(self, path, **kwargs):
            self.calls += 1
            return OCRResult(str(path), self.name, "test", 0, 100, 200, [])

    image = tmp_path / "page.jpg"
    image.write_bytes(b"image")
    engine = Engine()
    cache = CachedOCR(engine, tmp_path / "cache")
    cache.cache_file(image).write_text(broken, encoding="utf-8")
    assert not cache.cached(image)
    result = cache.recognise(image)
    assert cache.recognise(image) == result and engine.calls == 1
    assert cache.cached(image) and not list(cache.dir.glob("*.tmp"))


def test_failed_cache_replace_preserves_the_previous_result(tmp_path, monkeypatch):
    from ocr import cache as module

    class Engine:
        name = "test"

        def cache_tag(self):
            return ""

        def recognise(self, path, **kwargs):
            return OCRResult(str(path), self.name, "test", 0, 100, 200, [])

    image = tmp_path / "page.jpg"
    image.write_bytes(b"image")
    cache = module.CachedOCR(Engine(), tmp_path / "cache")
    result = cache.recognise(image)
    original = cache.cache_file(image).read_bytes()

    def interrupted(*args):
        raise OSError("simulated interrupted publish")

    monkeypatch.setattr(module.os, "replace", interrupted)
    with pytest.raises(OSError):
        cache.recognise(image, refresh=True)
    assert cache.cache_file(image).read_bytes() == original
    assert cache.recognise(image) == result and not list(cache.dir.glob("*.tmp"))


def test_ocr_result_round_trips_through_json():
    r = OCRResult("p.jpg", "tesseract", "5.4", 0.8, 100, 200,
                  [Word("x", 1, 2, 3, 4, 90.0, 1, 1101)])
    back = OCRResult.from_dict(json.loads(json.dumps(r.to_dict())))
    assert back == r
    assert back.words[0].right == 4
