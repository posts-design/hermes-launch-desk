import pytest

from launch_desk.host import SourceUnavailable
from launch_desk.setup_check import SetupIssue, check_setup


def _raises(exc):
    def probe():
        raise exc
    return probe


def codes(probe):
    return [i.code for i in check_setup(probe)]


def test_all_good_returns_empty():
    assert check_setup(lambda: {"allowed": True, "plan": "free"}) == []
    assert check_setup(lambda: {}) == []


def test_permission_error_from_probe_reports_allowlist():
    issues = check_setup(_raises(SourceUnavailable("not allowed", kind="permission")))
    assert [i.code for i in issues] == ["allowlist"]
    assert issues[0].command == "hermes config set plugins.entries.launch-desk.mcp_allowlist '[\"posts\"]'"


@pytest.mark.parametrize("message", [
    "MCP server 'posts' is not connected",
    "MCP server not found",
    "unknown server posts",
    "No MCP server named posts",
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
    RuntimeError("boom"),
])
def test_timeout_or_unknown_is_unreachable_with_no_command(exc):
    issues = check_setup(_raises(exc))
    assert [i.code for i in issues] == ["unreachable"]
    assert issues[0].command == ""


def test_issue_is_a_plain_record():
    assert SetupIssue("server", "m", "c").command == "c"
