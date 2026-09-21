from refs import primary_key, ref_keys, same_invoice


def test_inv_prefix_is_ignored():
    assert same_invoice("INV053241", "053241")


def test_letter_i_prefix_and_misread_one():
    assert same_invoice("I02657", "02657")
    assert same_invoice("102604", "I02604")


def test_genuine_leading_one_is_not_dropped():
    # Vassco numbers start with 1 and are not zero padded: 1844378 is not 844378
    assert not same_invoice("1844378", "844378")


def test_supplier_letter_prefix_may_be_left_off():
    assert same_invoice("FNLI30776", "30776")
    assert not same_invoice("FNLI30776", "FNLI30777")


def test_leading_zeros_do_not_matter():
    assert same_invoice("0057234", "57234")


def test_different_numbers_differ():
    assert not same_invoice("053241", "053242")


def test_punctuation_and_case():
    assert same_invoice("inv-053241", "INV 053241")


def test_empty():
    assert ref_keys(None) == frozenset()
    assert not same_invoice("", "")
    assert primary_key(None) == ""


def test_primary_key():
    assert primary_key("INV053241") == "53241"
    assert primary_key("SO477047") == "SO477047"
