"""The speed trial's sample rule and cascade decisions. No recognition models are loaded."""
from eval.trials.speed import (
    cascade_decision,
    choose_sample,
    latest_timings,
    needs_accurate,
    not_lower,
)


def _page(no, invoice_id):
    return {"page_no": no, "image_path": f"aug_p{no:03d}.jpg", "invoice_id": invoice_id}


def _match(invoice_id, supplier, status="matched"):
    return {"status": status, "invoice_id": invoice_id, "supplier": supplier,
            "pilot_supplier": supplier.upper(), "pilot_reference": invoice_id, "pilot_date": "2026-08-01"}


MANIFEST = [_page(1, "b_1"), _page(2, "a_1"), _page(3, "a_1"), _page(4, "b_2"), _page(5, "a_2"),
            _page(6, None), _page(7, "c_1")]
MATCHES = [_match("a_2", "a"), _match("a_1", "a"), _match("b_1", "b"), _match("b_2", "b"),
           _match("c_1", "c", status="scan_only"), {"status": "pilot_only", "invoice_id": None, "supplier": "c"}]
KEYED = {(m["pilot_supplier"], m["pilot_reference"], m["pilot_date"]): ["keyed line"]
         for m in MATCHES if m.get("pilot_supplier")}


def ids(sample):
    return [m["invoice_id"] for m, _ in sample]


def test_suppliers_take_turns_and_invoices_come_whole_in_binder_order():
    sample = choose_sample(MATCHES, MANIFEST, KEYED, target_pages=4)
    assert ids(sample) == ["a_1", "b_1", "a_2"]
    assert [p["page_no"] for p in sample[0][1]] == [2, 3]


def test_selection_stops_at_the_target_or_when_invoices_run_out():
    assert ids(choose_sample(MATCHES, MANIFEST, KEYED, target_pages=1)) == ["a_1"]
    assert sorted(ids(choose_sample(MATCHES, MANIFEST, KEYED, target_pages=100))) == ["a_1", "a_2", "b_1", "b_2"]


def test_scans_without_a_tie_or_keyed_lines_are_never_sampled():
    without_b2 = {k: v for k, v in KEYED.items() if k[1] != "b_2"}
    assert "b_2" not in ids(choose_sample(MATCHES, MANIFEST, without_b2, target_pages=100))
    assert "c_1" not in ids(choose_sample(MATCHES, MANIFEST, KEYED, target_pages=100))


def test_the_sample_does_not_depend_on_the_order_of_the_inputs():
    assert ids(choose_sample(list(reversed(MATCHES)), MANIFEST, KEYED, 4)) == ids(choose_sample(MATCHES, MANIFEST, KEYED, 4))


def test_cascade_reads_again_figures_doubtful_readings_and_the_header_region():
    assert needs_accurate("R 211.69", 0.99, top=900, page_height=1000)
    assert needs_accurate("Chicken breast", 0.50, top=900, page_height=1000)
    assert needs_accurate("Green Grocer", 0.99, top=100, page_height=1000)
    assert not needs_accurate("Chicken breast", 0.99, top=900, page_height=1000)


def test_cascade_continues_past_the_pilot_only_when_cheaper_than_b():
    rows = [{"kind": "page", "variant": v, "page": p, "cpu_s": cpu, "wall_s": cpu}
            for p in ("p1", "p2") for v, cpu in (("B", 10.0), ("C", 12.0))]
    assert cascade_decision(latest_timings(rows), ["p1", "p2"])["continue"] is False
    assert cascade_decision(latest_timings(rows), ["p1", "p3"]) is None
    retimed = rows + [{"kind": "page", "variant": "C", "page": p, "cpu_s": 5.0, "wall_s": 5.0} for p in ("p1", "p2")]
    assert cascade_decision(latest_timings(retimed), ["p1", "p2"])["continue"] is True


def test_agreement_must_hold_as_a_count_and_as_a_rate():
    a = {"agrees": 90, "compared": 100}
    assert not_lower({"agrees": 90, "compared": 100}, a, "agrees", "compared")
    assert not not_lower({"agrees": 89, "compared": 95}, a, "agrees", "compared")    # higher rate, fewer lines
    assert not not_lower({"agrees": 91, "compared": 110}, a, "agrees", "compared")   # more lines, lower rate
