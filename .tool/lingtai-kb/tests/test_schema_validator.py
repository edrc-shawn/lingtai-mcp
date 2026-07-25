# -*- coding: utf-8 -*-
"""测试灵台 Schema 硬校验"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema_validator import validate_page_path, validate_frontmatter, validate_page_create

VAULT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_valid_path():
    r = validate_page_path("丹房/07-工具与AI/测试页.md", VAULT)
    assert r.get("ok") is True


def test_invalid_path_no_domain():
    r = validate_page_path("丹房/测试页.md", VAULT)
    assert r.get("ok") is False
    assert "路径" in str(r.get("message", ""))

def test_invalid_path_no_digits():
    r = validate_page_path("丹房/AA-思考与认知/测试页.md", VAULT)
    assert r.get("ok") is False


def test_valid_frontmatter():
    content = "---\n标题: 测试\n日期: 2026-07-08\n---\n\n正文"
    r = validate_frontmatter(content, VAULT)
    assert r.get("ok") is True


def test_missing_frontmater_title():
    content = "---\n日期: 2026-07-08\n---\n\n正文"
    r = validate_frontmatter(content, VAULT)
    assert r.get("ok") is False


def test_missing_frontmatter():
    content = "\n\n正文"
    r = validate_frontmatter(content, VAULT)
    assert r.get("ok") is False


def test_page_create_valid():
    r = validate_page_create("新页面", "正文内容", "07-工具与AI", VAULT)
    assert r.get("ok") is True


def test_page_create_invalid_domain():
    r = validate_page_create("新页面", "正文", "no_domain", VAULT)
    assert r.get("ok") is False