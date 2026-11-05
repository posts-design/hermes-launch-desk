import json

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
