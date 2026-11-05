import json
import logging
from pathlib import Path

import pytest

from launch_desk.host import HermesHost, SourceUnavailable


class StubCtx:
    def __init__(self, reply=None, exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    def call_mcp(self, server, tool, arguments, timeout=None):
        self.calls.append((server, tool, arguments))
        if self.exc:
            raise self.exc
        return self.reply


def test_mcp_unwraps_envelope_to_dict():
    ctx = StubCtx({"ok": True, "result": json.dumps({"items": [1]})})
    assert HermesHost(ctx).mcp("search_references", {"query": "x"}) == {"items": [1]}
    assert ctx.calls[0][:2] == ("posts", "search_references")


@pytest.mark.parametrize("reply", [
    {"ok": False, "error": "signed out"},
    {"ok": True, "result": "not json"},
    {"ok": True, "result": "[1, 2]"},
    {"ok": True},
    "oops",
])
def test_mcp_bad_envelope_raises_source_unavailable(reply):
    with pytest.raises(SourceUnavailable):
        HermesHost(StubCtx(reply)).mcp("t", {})


@pytest.mark.parametrize("exc,kind", [
    (PermissionError("no"), "permission"),
    (TimeoutError("slow"), "timeout"),
    (RuntimeError("boom"), "error"),
])
def test_mcp_exceptions_become_source_unavailable_with_kind(exc, kind):
    with pytest.raises(SourceUnavailable) as info:
        HermesHost(StubCtx(exc=exc)).mcp("t", {})
    assert info.value.kind == kind
    assert info.value.__cause__ is exc


def test_data_dir_is_created(tmp_path):
    class Ctx:
        class state:
            data_dir = str(tmp_path / "a" / "b")
    assert HermesHost(Ctx()).data_dir().is_dir()


class LlmCtx:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.kwargs = None
        outer = self

        class llm:
            @staticmethod
            def complete_structured(**kwargs):
                outer.kwargs = kwargs
                if outer.exc:
                    raise outer.exc
                return outer.result

        self.llm = llm


class Result:
    def __init__(self, parsed=None, text=""):
        self.parsed = parsed
        self.text = text


def test_complete_json_returns_parsed_dict():
    ctx = LlmCtx(Result(parsed={"a": 1}, text='{"a": 1}'))
    assert HermesHost(ctx).complete_json("do it", "input", {"type": "object"}) == {"a": 1}
    assert ctx.kwargs["json_schema"] == {"type": "object"}
    assert ctx.kwargs["input"] == [{"type": "text", "text": "input"}]


def test_complete_json_falls_back_to_text_when_not_parsed():
    text = 'Here:\n```json\n{"a": 1}\n```'
    assert HermesHost(LlmCtx(Result(parsed=None, text=text))).complete_json("i", "t", {}) == text


@pytest.mark.parametrize("result", [Result(parsed=None, text=""), Result(parsed=None, text="  "), object()])
def test_complete_json_empty_reply_is_none(result):
    assert HermesHost(LlmCtx(result)).complete_json("i", "t", {}) is None


def test_complete_json_non_dict_parsed_falls_back_to_text():
    assert HermesHost(LlmCtx(Result(parsed=[1, 2], text="[1, 2]"))).complete_json("i", "t", {}) == "[1, 2]"


def test_complete_json_exception_is_none_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="launch_desk.host"):
        assert HermesHost(LlmCtx(exc=ValueError("schema"))).complete_json("i", "t", {}) is None
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert caplog.records[0].exc_info[0] is ValueError


def test_say_posts_message_and_never_raises():
    class Ctx:
        def __init__(self, exc=None):
            self.posted, self.exc = [], exc

        def post_message(self, text):
            if self.exc:
                raise self.exc
            self.posted.append(text)
            return object()

    ctx = Ctx()
    HermesHost(ctx).say("Reading the site.")
    assert ctx.posted == ["Reading the site."]
    HermesHost(Ctx(exc=RuntimeError("no gateway"))).say("x")  # no raise
    HermesHost(object()).say("x")  # a ctx without post_message: no raise


def _state_ctx(data_dir):
    class Ctx:
        class state:
            pass
    Ctx.state.data_dir = str(data_dir)
    return Ctx()


def test_media_path_copies_into_the_hermes_image_cache(tmp_path):
    home = tmp_path / "home"
    src = tmp_path / "card-x.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    out = Path(HermesHost(_state_ctx(home / "plugin-data" / "agent-plugin-x")).media_path(src, "card"))
    assert out.parent == home / "cache" / "images"
    assert out.name.startswith("launch-desk_") and out.name.endswith("_card.png")
    assert out.read_bytes() == src.read_bytes() and src.is_file()


def test_media_path_falls_back_to_the_original_outside_plugin_data(tmp_path):
    src = tmp_path / "card-x.png"
    src.write_bytes(b"png")
    assert HermesHost(_state_ctx(tmp_path / "a" / "b")).media_path(src, "card") == str(src)
    assert not (tmp_path / "cache").exists()


def test_media_path_needs_an_absolute_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "card-x.png"
    src.write_bytes(b"png")
    assert HermesHost(_state_ctx("home/plugin-data/ns")).media_path(src, "card") == str(src)
    assert not (tmp_path / "home" / "cache").exists()
