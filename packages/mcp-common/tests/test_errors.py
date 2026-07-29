"""Tests for mcp_common.errors."""

import json

from mcp_common.errors import error_json, safe_tool, tool_error


class TestToolError:
    def test_returns_dict_with_status_and_error(self):
        result = tool_error("something went wrong")
        assert result["status"] == "error"
        assert result["error"] == "something went wrong"

    def test_extra_kwargs_included(self):
        result = tool_error("bad", code=42, detail="extra")
        assert result["code"] == 42
        assert result["detail"] == "extra"

    def test_does_not_json_encode(self):
        result = tool_error("msg")
        assert isinstance(result, dict)


class TestErrorJson:
    def test_returns_valid_json_string(self):
        exc = ValueError("bad input")
        result = error_json(exc)
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "bad input" in parsed["error"]

    def test_extra_kwargs_included(self):
        exc = RuntimeError("boom")
        result = error_json(exc, context="during sync")
        parsed = json.loads(result)
        assert parsed["context"] == "during sync"

    def test_result_is_string(self):
        result = error_json(Exception("x"))
        assert isinstance(result, str)


class TestSafeTool:
    def test_passes_through_success(self):
        @safe_tool
        def fn(x: str) -> str:
            return json.dumps({"status": "ok", "value": x})

        result = fn("hello")
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["value"] == "hello"

    def test_catches_exception_and_returns_error_json(self):
        @safe_tool
        def fn() -> str:
            raise ValueError("oops")

        result = fn()
        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "oops" in parsed["error"]

    def test_preserves_function_name(self):
        @safe_tool
        def my_named_fn() -> str:
            return "{}"

        assert my_named_fn.__name__ == "my_named_fn"

    def test_result_is_string_on_error(self):
        @safe_tool
        def fn() -> str:
            raise RuntimeError("fail")

        result = fn()
        assert isinstance(result, str)
        json.loads(result)  # must be valid JSON
