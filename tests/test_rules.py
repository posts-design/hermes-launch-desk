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


@pytest.mark.parametrize("text", ["Launch Desk runs on localhost too.", "Hermes now binds 0.0.0.0 by default.",
                                  "Version 10.0.0.1 is out.", "Works with names like printer.local too."])
def test_prose_mentions_of_local_hosts_are_not_links(text):
    assert "private-link" not in ids(text)


@pytest.mark.parametrize("text,found", [("Try it: 127.0.0.1:8731/k3/", "127.0.0.1:8731"),
                                        ("Run it at localhost:3000", "localhost:3000"),
                                        ("Open printer.local/status", "printer.local")])
def test_bare_local_links_with_a_port_or_path_fail(text, found):
    [f] = [f for f in check(text, network="x") if f.rule_id == "private-link"]
    assert f.found == found


def test_watch_this_is_a_stock_phrase():
    assert "stock-phrase" in ids("Are you a founder? Stop pasting. Watch this.")


@pytest.mark.parametrize("link", ["http://127.1:8080/", "http://2130706433/", "http://10.1/"])
def test_short_and_decimal_ipv4_links_fail(link):
    assert "private-link" in ids(f"Themes are live.\n{link}")



# R40: the founder's own words never count as copied.

REF_SHARED = ["We kept pasting our own launch posts into Claude by hand, so we built this."]


def test_a_run_inside_the_founders_words_is_not_copied():
    text = "We kept pasting our own launch posts into Claude by hand.\n\nNow search 5,000+ of them."
    assert "copied-run" in ids(text, reference_captions=REF_SHARED)
    assert "copied-run" not in ids(text, reference_captions=REF_SHARED,
                                   own_words=["we kept pasting our own launch posts into Claude by hand"])


def test_the_founders_words_do_not_excuse_other_copied_runs():
    ref = REF_SHARED + ["Search every launch post that ever worked for you."]
    text = "We kept pasting our own posts.\n\nSearch every launch post that ever worked, here."
    assert "copied-run" in ids(text, reference_captions=ref, own_words=["we kept pasting our own posts"])


# ------------------------------------------------------------------ repeated phrases (Task 22)

BROOKHOUR = ("People kept asking to see their deep work next to their meetings.\n\n"
             "A focus session sits next to a meeting in your calendar.\n\n"
             "We put your deep work next to meetings in Brookhour 2.0.\n\nhttp://127.0.0.1:8731/k2/")
GOOD_POSTS = [  # live runs the judges read as fine: K1 j3k1, K3 j2k3, and a pawr post with no echo
    ("We kept pasting our own launch posts into Claude by hand.\n\nWe shipped posts.design MCP. Your agent can "
     "search 5,000+ real launch posts, OG images and Headers.\n\nhttps://posts.design/mcp", "posts.design"),
    ("Most of our support emails were blurry photos of sad leaves.\n\nFernlight reads a leaf photo and names the "
     "likely cause.\n\nYou get three things to do today.\n\nhttp://127.0.0.1:8731/k3/", "Fernlight"),
    ("Pet pages look the same. We built themes to match your site.\n\nYou can now pick a theme in "
     "settings.\n\nA small thing, but it makes the page feel like yours.\n\nhttps://pawr.link", "pawr.link"),
]
PAWR_ECHO = ("People kept asking to match their pet page to their own site.\n\nPeople kept asking for it.\n\n"
             "Custom themes for pet pages are live.\n\nhttps://pawr.link")
K2_RUN3 = ("People kept asking to see their deep work next to their meetings.\n\nBrookhour 2.0 is out. Focus "
           "sessions now show up in your calendar.\n\nA calm focus timer for Mac, so deep work sits next to "
           "your meetings.\n\nhttp://127.0.0.1:8731/k2/")


def test_brookhour_repeats_a_phrase_across_lines_and_names_it():
    [f] = [f for f in check(BROOKHOUR, network="x", product="Brookhour") if f.rule_id == "repeated-phrase"]
    assert f.found == "next meeting" and f.found in f.fix


@pytest.mark.parametrize("text,product", GOOD_POSTS)
def test_the_good_live_posts_repeat_nothing(text, product):
    assert not {"repeated-phrase", "repeats-first-line"} & ids(text, product=product)


def test_a_word_in_three_lines_repeats():
    text = "Themes are here.\n\nPick a colour theme.\n\nEvery theme follows your site."
    [f] = [f for f in check(text, network="x") if f.rule_id == "repeated-phrase"]
    assert f.found == "themes"


def test_the_product_name_may_appear_in_every_line():
    text = "Fernlight is out.\n\nFernlight reads a leaf.\n\nGet Fernlight on iPhone."
    assert "repeated-phrase" not in ids(text, product="Fernlight")
    assert "repeated-phrase" in ids(text)


def test_a_phrase_shared_only_with_the_first_line_is_not_this_rule():
    text = "Deep work next to meetings.\n\nYour calendar shows deep work now."
    assert "repeated-phrase" not in ids(text)


@pytest.mark.parametrize("text,product,phrase", [(PAWR_ECHO, "pawr.link", "people kept asking"),
                                                 (K2_RUN3, "Brookhour", "deep work next")])
def test_a_later_line_echoing_three_words_of_line_one_repeats(text, product, phrase):
    [f] = [f for f in check(text, network="x", product=product) if f.rule_id == "repeated-phrase"]
    assert f.found == phrase


def test_two_words_shared_with_line_one_are_fine():
    assert "repeated-phrase" not in ids("Deep work next to meetings.\n\nYour calendar shows deep work now.")


# ------------------------------------------------------------------ lower-case line starts (R42)

K3_RUN3 = ("Most of our support emails were blurry photos of sad leaves.\n\nso we shipped photo diagnosis. snap a leaf."
           "\n\nyou get the likely cause and three things to do today.\n\nhttp://127.0.0.1:8731/k3/")


def test_a_line_starting_lower_case_fails_and_is_named():
    [f] = [f for f in check(K3_RUN3, network="x", product="Fernlight") if f.rule_id == "lower-start"]
    assert f.found == "so we shipped"
    assert "lower-start" in ids("themes are here.\n\nPick one.")


@pytest.mark.parametrize("text,product", [
    ("Themes are live.\n\nhttps://pawr.link", ""),
    ("Themes are live.\n\n@biscuit asked first.", ""),
    ("pawr.link pages take themes now.\n\nPick one.", "pawr.link"),
    ("posts.design MCP is live.", "posts.design"),
    ("iPhone owners asked first.", ""),
    ("Themes are live.\n\n- Pick one in settings.\n\n3 themes to start.", ""),
    ("Themes are live.\n\nfernlight.app has the rest.", ""),
])
def test_links_handles_and_brands_may_start_lower_case(text, product):
    assert "lower-start" not in ids(text, product=product)


def test_lower_case_voice_relaxes_the_rule():
    voice = ["shipped a small thing today. pet pages load faster."]
    assert "lower-start" not in ids("themes are here.", voice_samples=voice)
    assert next(r for r in RULES if r.id == "lower-start").relaxable


# ------------------------------------------------------------------ the post's own words (I1)

@pytest.mark.parametrize("text,phrase", [
    ("Brookhour 2.0 is out.\n\nStart a focus session from the menu bar.\n\nEach focus session lands on your calendar.",
     "focus session"),
    ("Status pages are live.\n\nOur status pages load fast.\n\nShare status pages with anyone.", "status pages"),
])
def test_the_repeated_phrase_is_quoted_in_the_posts_own_words(text, phrase):
    [f] = [f for f in check(text, network="x", product="Brookhour") if f.rule_id == "repeated-phrase"]
    assert f.found == phrase and f"\"{phrase}\"" in f.fix


def test_bullet_items_may_start_lower_case():
    assert "lower-start" not in ids("Themes are live.\n\n- faster search\n- pick a theme\n• dark mode too")


def test_a_voice_sample_opening_with_the_lower_case_brand_does_not_relax_capitals():
    from launch_desk.rules import relaxed_rules
    voice = ["tldraw now exports to video."]
    assert "lower-start" in relaxed_rules(voice)
    assert "lower-start" not in relaxed_rules(voice, product="tldraw")
    assert "lower-start" not in ids("pick a theme.", voice_samples=["we shipped a thing."], product="pawr.link")


# ------------------------------------------------------------------ echoes (R47)

from launch_desk.rules import echoes  # noqa: E402


def test_echoes_finds_a_shared_three_word_phrase_in_the_references_words():
    founder = "People kept asking to see their deep work next to their meetings."
    assert echoes("You asked for deep work next to meetings. It's here.", founder) == "deep work next"


def test_echoes_finds_half_the_content_words():
    assert echoes("Deep focus beats meetings, finally.", "Finally, deep focus.")


def test_naming_what_shipped_is_never_an_echo():
    founder = "We finally shipped offline mode for notes."
    line = "Offline mode keeps every note on your device until you reconnect."
    assert echoes(line, founder)
    assert echoes(line, founder, product="Jotter", what="offline mode for notes") == ""


def test_pawr_and_k2_echoes_still_fail_with_what_shipped_left_out():
    assert [f.found for f in check(PAWR_ECHO, network="x", product="pawr.link",
                                   what="custom themes for pet profile pages") if f.rule_id == "repeated-phrase"] \
        == ["people kept asking"]
    assert [f.found for f in check(K2_RUN3, network="x", product="Brookhour",
                                   what="Brookhour 2.0: focus sessions now show up in your calendar")
            if f.rule_id == "repeated-phrase"] == ["deep work next"]


def test_the_repeated_phrase_message_fits_what_repeated():
    def message(text):
        return next(f.message for f in check(text, network="x") if f.rule_id == "repeated-phrase")
    pair = message("Themes are live.\n\nPick a colour theme.\n\nThe colour theme follows you.")
    three = message("Themes are here.\n\nPick a theme.\n\nEvery theme follows your site.")
    first = message("Deep work sits next to meetings.\n\nDeep work next to every meeting, finally.")
    assert len({pair, three, first}) == 3
