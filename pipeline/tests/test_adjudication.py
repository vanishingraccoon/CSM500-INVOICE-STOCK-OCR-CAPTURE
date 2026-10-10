import pytest

from app import adjudication, data


def test_classify_kinds_of_disagreement():
    assert adjudication.classify({"item_code": None}, 5.0, {"1"}) == "refused"
    assert adjudication.classify({"item_code": "2"}, 5.0, {"1"}) == "item"
    assert adjudication.classify({"item_code": "1", "base_quantity": 10.0}, 5.0, {"1"}) == "quantity"


def test_agreement_and_non_stock_entries_are_not_disagreements():
    assert adjudication.classify({"item_code": "1", "base_quantity": 5.0}, 5.0, {"1"}) is None
    assert adjudication.classify({"item_code": "1", "base_quantity": 10.0}, 5.0, set()) is None


def test_latest_verdict_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "REVIEW", tmp_path / "review")
    item = {"key": "inv1:0", "kind": "quantity", "supplier": "vassco"}
    adjudication.record(item, "pilot")
    adjudication.record(item, "system", "checked the paper again")
    decided = adjudication.verdicts()
    assert decided["inv1:0"]["verdict"] == "system" and decided["inv1:0"]["note"] == "checked the paper again"
    assert adjudication.summary([item, {"key": "inv2:1", "kind": "item", "supplier": None}], decided) == \
        {"quantity": {"system": 1}, "item": {"undecided": 1}}


def test_disagreements_are_rebuilt_when_uploads_or_pilot_exports_change(tmp_path, monkeypatch):
    import config
    for name in ("OUT", "GT", "PILOT"):
        (tmp_path / name).mkdir()
        monkeypatch.setattr(config, name, tmp_path / name)
    monkeypatch.setattr(data, "UPLOADS", tmp_path / "OUT" / "uploads")
    before = adjudication._stamp()
    (tmp_path / "PILOT" / "purchasing_report_x.csv").write_text("a", encoding="utf-8")
    after_pilot = adjudication._stamp()
    data.UPLOADS.mkdir()
    (data.UPLOADS / "upload_1.json").write_text("{}", encoding="utf-8")
    after_upload = adjudication._stamp()
    assert before != after_pilot != after_upload


def test_unknown_verdict_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "REVIEW", tmp_path / "review")
    with pytest.raises(ValueError):
        adjudication.record({"key": "k", "kind": "item", "supplier": None}, "maybe")
