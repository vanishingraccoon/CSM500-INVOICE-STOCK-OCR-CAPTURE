from argparse import Namespace
from pathlib import Path

import pytest

import pipeline


def names(**flags):
    args = Namespace(ocr=False, hwr=False, pdf=Path("binder.pdf"), workers=4, **flags) if not flags else \
        Namespace(**{"ocr": False, "hwr": False, "pdf": Path("binder.pdf"), "workers": 4, **flags})
    return [n for n, _, _ in pipeline.steps(args)]


def test_default_run_uses_the_ocr_cache_and_ends_with_the_report():
    assert names() == ["ingest", "parser", "normalise", "checks", "groundtruth", "report"]


def test_optional_steps_are_added_in_order():
    assert names(ocr=True, hwr=True) == ["extract", "ocr", "ingest", "parser", "normalise", "checks", "groundtruth",
                                        "hwr-regions", "hwr-trocr", "hwr-trial", "report"]


def test_trocr_runs_in_its_own_environment():
    steps = pipeline.steps(Namespace(ocr=False, hwr=True, pdf=Path("b.pdf"), workers=4))
    trocr = next(python for name, _, python in steps if name == "hwr-trocr")
    assert trocr.parent.parent.name == ".venv-hwr"


def test_unknown_start_step_is_refused():
    with pytest.raises(SystemExit):
        pipeline.main(["--from", "nope"])
