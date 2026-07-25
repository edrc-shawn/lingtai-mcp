# -*- coding: utf-8 -*-
"""
灵台三级降级——自检子系统可用性，注入降级模式标记。

在 context_load 返回时自动检测并注入 layes.mode 字段。
Agent 应检查 mode.level，在 L1/L2 时不调用不可用的子系统。
"""
import os


def check_mode(vault_path: str) -> dict:
    """自检并返回当前降级模式"""
    wiki_ok = os.path.isdir(os.path.join(vault_path, "丹房")) and any(
        f.endswith(".md") for root, _dirs, files in os.walk(os.path.join(vault_path, "丹房"))
        for f in files
    )
    bank_ok = os.path.exists(
        os.path.join(vault_path, ".tool", "lingtai-kb", "memory_bank", "data", "memories.json")
    )
    profile_ok = os.path.exists(os.path.join(vault_path, "画像", "履历.md"))

    if wiki_ok and bank_ok and profile_ok:
        return {
            "level": "L0",
            "label": "全功能",
            "marker": "[灵台·L0]",
            "unavailable": [],
            "hint": "全部子系统正常运行",
        }
    elif profile_ok:
        # 画像可用就算保底——记忆银行有 fallback
        return {
            "level": "L1",
            "label": "降级·无知识库",
            "marker": "[灵台·L1 无知识库]",
            "unavailable": ([] if bank_ok else ["记忆银行"])
            + (["丹房", "原料"] if not wiki_ok else []),
            "hint": "部分文件系统不可访问"
            if not wiki_ok
            else "记忆银行文件损坏",
        }
    else:
        return {
            "level": "L2",
            "label": "最小模式",
            "marker": "[灵台·L2 最小模式]",
            "unavailable": [s for s, ok in
                [("丹房", wiki_ok), ("记忆银行", bank_ok), ("画像", profile_ok)]
                if not ok],
            "hint": "核心资源不可用，仅规则可用",
        }