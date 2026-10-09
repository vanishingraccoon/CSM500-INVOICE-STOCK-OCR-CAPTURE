from ocr.paddle import _merge_word_tokens, _split_line, words_from_result


def box(l, r, t=10, b=30):
    return ((l, t), (r, t), (r, b), (l, b))


def test_tokens_between_space_tokens_join_into_words():
    tokens = ["TAX", " ", "INVOICE"]
    regions = [box(0, 30), box(30, 40), box(40, 110)]
    words = _merge_word_tokens(tokens, regions)
    assert [w for w, _ in words] == ["TAX", "INVOICE"]
    assert words[1][1][0] == 40


def test_character_tokens_join():
    tokens = list("I02657")
    regions = [box(i * 10, i * 10 + 9) for i in range(6)]
    words = _merge_word_tokens(tokens, regions)
    assert words == [("I02657", (0.0, 10.0, 59.0, 30.0))]


def test_leading_space_in_token_starts_a_new_word():
    tokens = ["Trading", " (Pty)Ltd"]
    regions = [box(0, 70), box(70, 160)]
    assert [w for w, _ in _merge_word_tokens(tokens, regions)] == ["Trading", "(Pty)Ltd"]


def test_run_with_internal_space_is_split_proportionally():
    tokens = ["No: 4830308500"]
    regions = [box(0, 140)]
    words = _merge_word_tokens(tokens, regions)
    assert [w for w, _ in words] == ["No:", "4830308500"]
    assert words[0][1][2] < words[1][1][0]


def test_split_line_covers_the_box():
    parts = _split_line("2 x 12.5kg", (0, 0, 100, 20))
    assert [p for p, _ in parts] == ["2", "x", "12.5kg"]
    assert parts[-1][1][2] <= 100.0001


def test_words_from_result_offsets_crop_and_scales_confidence():
    res = {"rec_texts": ["TAX INVOICE"], "rec_scores": [0.97], "rec_boxes": [[0, 10, 110, 30]],
           "text_word": [["TAX", " ", "INVOICE"]], "text_word_region": [[box(0, 30), box(30, 40), box(40, 110)]]}
    words = words_from_result(res, dx=100, dy=1000)
    assert [(w.text, w.left, w.top) for w in words] == [("TAX", 100, 1010), ("INVOICE", 140, 1010)]
    assert words[0].conf == 97.0
