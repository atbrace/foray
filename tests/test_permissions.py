from foray.permissions import DEFAULT_TOOLS, resolve_tools


def test_default_tools():
    result = resolve_tools()
    assert set(result) == set(DEFAULT_TOOLS)


def test_allow_adds_tools():
    result = resolve_tools(allow=["mcp__playwright"])
    assert "mcp__playwright" in result
    assert "Read" in result


def test_deny_removes_tools():
    result = resolve_tools(deny=["WebFetch", "WebSearch"])
    assert "WebFetch" not in result
    assert "WebSearch" not in result
    assert "Read" in result


def test_allow_and_deny():
    result = resolve_tools(allow=["CustomTool"], deny=["Bash"])
    assert "CustomTool" in result
    assert "Bash" not in result


def test_deny_nonexistent_is_noop():
    result = resolve_tools(deny=["NonexistentTool"])
    assert set(result) == set(DEFAULT_TOOLS)
