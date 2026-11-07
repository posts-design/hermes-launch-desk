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


REPEAT = "Check this out: your pet's page can now match your site.\nNow your pet's page can match your own site."


def test_restating_the_first_line_fails():
    assert "repeats-first-line" in ids(REPEAT)


def test_second_line_with_new_information_passes():
    text = "Pet pages can wear your colours now.\nPick a theme in settings and the whole page follows."
    assert "repeats-first-line" not in ids(text)


def test_url_only_line_never_repeats():
    text = "Pet pages can wear your colours now.\nhttps://pawr.link/pet-pages-can-wear-your-colours-now"
    assert "repeats-first-line" not in ids(text)


def test_short_first_line_never_repeats():
    assert "repeats-first-line" not in ids("Themes are here.\nThemes are here.")


def test_repeats_first_line_is_not_relaxable():
    assert "repeats-first-line" in ids(REPEAT, voice_samples=[REPEAT])


# ------------------------------------------------------------------ template opener

import pytest  # noqa: E402

from launch_desk.rules import RULES, STOCK_PHRASES, repeating_lines  # noqa: E402


@pytest.mark.parametrize("first", ["Your agent should pull real launch posts.",
                                   "your focus sessions should sit next to meetings.",
                                   "YOUR sad leaf SHOULD get a straight answer."])
def test_your_x_should_opener_fails(first):
    assert "template-opener" in ids(first + "\nMore here.")


@pytest.mark.parametrize("text", ["Your agent can now pull real launch posts.",
                                  "Pet pages take themes now.\nYour page should match your site.",
                                  "Your very own big page should load."])
def test_other_openers_pass_the_template_rule(text):
    assert "template-opener" not in ids(text)


@pytest.mark.parametrize("phrase", ["Check this out: themes.", "Say hello to themes.", "Meet the new pet page."])
def test_new_stock_phrases_fail(phrase):
    assert "stock-phrase" in ids(phrase)


def test_stock_phrase_failure_quotes_the_phrase():
    [f] = [f for f in check("Pet pages, check this OUT: themes.", network="x") if f.rule_id == "stock-phrase"]
    assert f.found.lower() == "check this out"


# ------------------------------------------------------------------ private links

@pytest.mark.parametrize("link", [
    "http://127.0.0.1:8731/k3/", "http://localhost:3000/", "https://app.localhost/x",
    "http://10.1.2.3/", "http://172.16.0.1/", "http://172.31.255.1/a", "http://192.168.1.5:8080",
    "http://169.254.10.20/", "http://[::1]:8080/", "http://[fe80::1]/", "https://printer.local/status",
    "http://0.0.0.0:8000/",
])
def test_private_links_fail(link):
    failures = [f for f in check(f"Themes are live.\nTry it: {link}", network="x") if f.rule_id == "private-link"]
    assert len(failures) == 1 and failures[0].found and failures[0].found in link


def test_bare_loopback_address_fails():
    assert "private-link" in ids("Themes are live.\nTry it: 127.0.0.1:8731/k3/")


@pytest.mark.parametrize("link", ["https://posts.design/mcp", "http://8.8.8.8/", "http://172.32.0.1/",
                                  "https://localhost.example.com/", "https://notlocal.dev/",
                                  "https://local.example/"])
def test_public_links_pass(link):
    assert "private-link" not in ids(f"Themes are live.\n{link}")


def test_new_rules_are_strict():
    by_id = {r.id: r for r in RULES}
    assert not by_id["template-opener"].relaxable and not by_id["private-link"].relaxable
    assert {"check this out", "say hello to", "meet the new"} <= set(STOCK_PHRASES)


def test_repeating_lines_names_the_lines_that_restate_the_first():
    text = "Most support emails were blurry leaf photos.\n\nNow snap a leaf.\nOur support emails were blurry leaf photos."
    assert repeating_lines(text) == [3]
    assert repeating_lines("Short.\nShort.") == []


def test_a_shared_link_is_not_a_copied_run():
    # the founder's own past post ends on the same product link; links are not wording
    ref = ["Pick one and the page follows.\n\nhttps://pawr.link"]
    text = "Dark mode is here.\n\nPick a theme and the whole page follows.\n\nhttps://pawr.link"
    assert "copied-run" not in ids(text, reference_captions=ref)
    assert "copied-run" in ids("We said pick one and the page follows today. https://pawr.link",
                               reference_captions=ref)
