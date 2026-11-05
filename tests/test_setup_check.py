import pytest

from launch_desk.host import SourceUnavailable
from launch_desk.setup_check import ADD_SERVER, ALLOWLIST, SetupIssue, check_setup


def _raises(exc):
    def probe():
        raise exc
    return probe


def codes(probe):
    return [i.code for i in check_setup(probe)]


def test_all_good_returns_empty():
    assert check_setup(lambda: {"allowed": True, "plan": "free"}) == []
    assert check_setup(lambda: {}) == []


@pytest.mark.parametrize("exc", [SourceUnavailable("not allowed", kind="permission"), PermissionError("no")])
def test_permission_error_gives_add_server_then_allowlist(exc):
    # Hermes checks the allowlist before the server exists, so a permission error can also
    # mean no server: both commands, add first.
    issues = check_setup(_raises(exc))
    assert [i.code for i in issues] == ["allowlist"]
    lines = issues[0].command.splitlines()
    assert lines == [ADD_SERVER, ALLOWLIST]


@pytest.mark.parametrize("message", [
    "MCP server 'posts' is not connected",
])
def test_no_server_reports_server_issue(message):
    issues = check_setup(_raises(SourceUnavailable(message, kind="error")))
    assert [i.code for i in issues] == ["server"]
    assert issues[0].command == "hermes mcp add posts --url https://posts.design/mcp --auth oauth"


@pytest.mark.parametrize("message", [
    "HTTP 401",
    "Unauthorized",
    "MCP server 'posts' requires re-authentication. Run `hermes mcp login posts`",
    "token expired",
    "MCP server 'posts' requires re-authentication. client not found Run `hermes mcp login posts`",
])
def test_401_from_probe_reports_signin(message):
    issues = check_setup(_raises(SourceUnavailable(message, kind="error")))
    assert [i.code for i in issues] == ["signin"]
    assert issues[0].command == "hermes mcp login posts"


def test_allowed_false_reports_signin():
    assert codes(lambda: {"allowed": False}) == ["signin"]


@pytest.mark.parametrize("exc", [
    SourceUnavailable("timed out", kind="timeout"),
    SourceUnavailable("something odd", kind="error"),
    SourceUnavailable("MCP server 'posts' transport is down; reconnect requested. Do NOT retry this tool immediately", kind="error"),
    SourceUnavailable("MCP server 'posts' is unreachable after 3 consecutive failures. Paused", kind="error"),
    RuntimeError("boom"),
])
def test_timeout_or_unknown_is_unreachable_with_no_command(exc):
    issues = check_setup(_raises(exc))
    assert [i.code for i in issues] == ["unreachable"]
    assert issues[0].command == ""


def test_issue_is_a_plain_record():
    assert SetupIssue("server", "m", "c").command == "c"
