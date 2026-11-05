from launch_desk.rules import check, x_length


def ids(text, **kw):
    kw.setdefault("network", "x")
    return {f.rule_id for f in check(text, **kw)}


def line(n):
    return " ".join(["word"] * n)


def test_first_line_of_13_words_fails():
    assert "first-line-short" in ids(line(13))


def test_first_line_of_12_words_passes():
    assert "first-line-short" not in ids(line(12))


def test_stock_phrase_case_insensitive():
    assert "stock-phrase" in ids("We're EXCITED TO ANNOUNCE a thing")


def test_bare_introducing_is_stock_phrase():
    assert "stock-phrase" in ids("Introducing Foo")


def test_introducing_with_clause_passes():
    assert "stock-phrase" not in ids("Introducing Foo, which now exports to PDF in one click")


def test_two_em_dashes_fail_one_passes():
    assert "em-dash" in ids("a — b — c")
    assert "em-dash" not in ids("a — b")


def test_two_emoji_fail():
    assert "emoji" in ids("ship \U0001f680\U0001f389")


def test_zwj_sequence_counts_as_one():
    assert "emoji" not in ids("ship \U0001f469‍\U0001f4bb")


def test_three_hashtags_fail():
    assert "hashtags" in ids("a #one #two #three")


def test_bracket_placeholder_fails():
    for t in ("see [link]", "hi {name}", "TODO fix"):
        assert "placeholder" in ids(t)


def test_link_in_first_line_fails():
    assert "link-first-line" in ids("Shipped https://x.com/a today\nmore")


def test_link_on_later_line_passes():
    assert "link-first-line" not in ids("Shipped today\nhttps://x.com/a")


def test_x_length_counts_urls_as_23():
    text = "a" * 260 + " https://example.com/" + "b" * 79
    assert x_length(text) == 284
    assert "length" in ids(text)


def test_url_counts_23_not_real_length():
    text = "a" * 250 + " https://example.com/" + "b" * 200
    assert x_length(text) == 274
    assert "length" not in ids(text)


def test_length_ignored_on_linkedin():
    assert "length" not in ids("a" * 400, network="linkedin")


def test_five_word_run_from_reference_fails():
    ref = ["We Shipped the new editor today, finally."]
    assert "copied-run" in ids("yes, shipped THE new editor today!", reference_captions=ref)


def test_four_word_run_passes():
    ref = ["We shipped the new editor today"]
    assert "copied-run" not in ids("shipped the new editor", reference_captions=ref)


def test_voice_with_emoji_relaxes_emoji_rule():
    voice = ["love it \U0001f680\U0001f389"]
    assert "emoji" in ids("a \U0001f680\U0001f389")
    assert "emoji" not in ids("a \U0001f680\U0001f389", voice_samples=voice)


def test_stock_phrases_never_relaxed():
    voice = ["We are thrilled to say hi \U0001f680\U0001f389"]
    assert "stock-phrase" in ids("thrilled to share", voice_samples=voice)


def test_stock_phrase_matches_a_curly_apostrophe():
    ids = [f.rule_id for f in check("In today\u2019s fast-paced world, themes ship.", network="x")]
    assert "stock-phrase" in ids
