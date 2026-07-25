# -*- coding: utf-8 -*-
"""
灵台 MCP Server 测试 fixtures
=============================
提供最小化 vault 环境 + server 实例，供 smoke test 使用。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 确保 lingtai-kb 目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def vault_dir():
    """创建最小化 vault 目录结构（session 级复用）"""
    with tempfile.TemporaryDirectory(prefix="lingtai_test_", ignore_cleanup_errors=True) as tmp:
        vault = Path(tmp)
        # 丹房（知识库）
        danfang = vault / "丹房" / "00-思考与认知"
        danfang.mkdir(parents=True)
        (vault / "丹房" / ".meta").mkdir(parents=True)
        # 写一个测试页
        test_page = danfang / "测试页面.md"
        test_page.write_text(
            "---\n标题: 测试页面\n日期: 2026-07-24\n---\n\n# 测试页面\n\n这是测试内容。\n\n[[含人量]]\n",
            encoding="utf-8",
        )
        # 最小 index.json
        index = {
            "pages": [
                {
                    "path": "丹房/00-思考与认知/测试页面.md",
                    "filename": "测试页面.md",
                    "title": "测试页面",
                    "domain": "00-思考与认知",
                    "tags": [],
                    "summary": "这是测试内容。",
                    "content": "这是测试内容。",
                    "links_to": ["含人量"],
                    "linked_from": [],
                    "content_hash": "abc123",
                    "body_hash": "def456",
                    "_tl": "测试页面",
                    "_sl": "这是测试内容。",
                    "_tl2": "",
                    "_ng3": [],
                }
            ],
            "name_to_path": {"测试页面": "丹房/00-思考与认知/测试页面.md"},
            "built_at": "2026-07-24T00:00:00",
        }
        (vault / "丹房" / ".meta" / "index.json").write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8"
        )

        # 原料
        raw_dir = vault / "原料"
        raw_dir.mkdir()
        (raw_dir / "测试原料.md").write_text(
            "---\n标题: 测试原料\n状态: 待提炼\n---\n\n# 测试原料\n\n原料内容。\n",
            encoding="utf-8",
        )

        # 画像
        profile_dir = vault / "画像"
        profile_dir.mkdir()
        (profile_dir / "履历.md").write_text("---\n标题: 履历\n---\n\n测试履历。\n", encoding="utf-8")
        (profile_dir / "心性.md").write_text("---\n标题: 心性\n---\n\n测试心性。\n", encoding="utf-8")
        (profile_dir / "我是谁.md").write_text("---\n标题: 我是谁\n---\n\n测试存在。\n", encoding="utf-8")

        # 入门
        (vault / "入门").mkdir()

        # .tool/lingtai-kb 数据目录
        tool_dir = vault / ".tool" / "lingtai-kb"
        tool_dir.mkdir(parents=True)
        (tool_dir / "observation").mkdir()
        (tool_dir / "logs").mkdir()
        (tool_dir / "cache").mkdir()
        (tool_dir / "data").mkdir()
        (tool_dir / "weights").mkdir()
        (tool_dir / "signals").mkdir()
        (tool_dir / "profile").mkdir()

        # memory_bank 数据目录
        mb_dir = tool_dir / "memory_bank" / "data"
        mb_dir.mkdir(parents=True)
        (mb_dir / "memories.json").write_text("[]", encoding="utf-8")

        # 技能目录
        (vault / "技能").mkdir()

        # 作品目录
        (vault / "作品").mkdir()

        yield str(vault)


@pytest.fixture(scope="session")
def server(vault_dir):
    """创建 LingtaiMCPServer 实例（session 级复用）"""
    os.environ["LINGTAI_VAULT"] = vault_dir
    from server import LingtaiMCPServer

    srv = LingtaiMCPServer(vault_path=vault_dir)
    srv.set_client("pytest")
    return srv


@pytest.fixture(scope="session")
def tool_map(server):
    """获取 _TOOL_MAP（所有已注册工具的 handler）"""
    import router

    # 替换 router 中的 server 实例为测试实例
    router.server = server
    # 重建 _TOOL_MAP
    from decorators import REGISTRY

    tool_map = {}
    for name, info in REGISTRY.items():
        tool_map[name] = router._build_handler(name, info)
    return tool_map
