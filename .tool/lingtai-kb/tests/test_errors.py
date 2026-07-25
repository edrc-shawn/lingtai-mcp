# -*- coding: utf-8 -*-
"""测试灵台标准化错误码模块"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from errors import ok, fail, ErrorCode


def test_ok_structure():
    r = ok({"pages": 197})
    assert r["ok"] is True
    assert r["data"]["pages"] == 197
    assert "code" not in r


def test_fail_structure():
    r = fail(ErrorCode.KB_NOT_FOUND, "页面不存在")
    assert r["ok"] is False
    assert r["code"] == "kb_not_found"
    assert r["message"] == "页面不存在"


def test_fail_without_message():
    r = fail(ErrorCode.REGISTRY_CONFLICT)
    assert r["ok"] is False
    assert r["code"] == "registry_conflict"
    assert "message" not in r


def test_fail_with_data():
    r = fail(ErrorCode.SCHEMA_VIOLATION, "格式违规", data={"violations": ["路径不对"]})
    assert r["ok"] is False
    assert r["code"] == "schema_violation"
    assert r["data"]["violations"][0] == "路径不对"


def test_all_codes_unique():
    codes = [e.value for e in ErrorCode]
    assert len(codes) == len(set(codes))


def test_wrap_standard():
    from router import _wrap
    r = _wrap({"ok": True, "data": {"n": 1}})
    assert r["ok"] is True
    assert r["data"]["n"] == 1


def test_wrap_raw_dict():
    from router import _wrap
    r = _wrap({"results": ["a", "b"]})
    assert r["ok"] is True
    assert r["data"]["results"] == ["a", "b"]


def test_wrap_fail():
    from router import _wrap
    r = _wrap(fail(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在"))
    assert r["ok"] is False
    assert r["code"] == "memory_not_found"