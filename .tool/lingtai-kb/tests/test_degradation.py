# -*- coding: utf-8 -*-
"""测试灵台三级降级"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from degradation import check_mode

VAULT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def test_l0_full():
    mode = check_mode(VAULT)
    assert mode["level"] == "L0"
    assert mode["unavailable"] == []
    assert "[灵台·L0]" in mode["marker"]
    assert "hint" in mode  # L0 有 hint 字段但内容无关


def test_l2_on_nonexistent_path():
    mode = check_mode(r"C:\nonexistent\path")
    assert mode["level"] == "L2"


def test_l1_when_wiki_missing():
    """用不存在的路径模拟丹房不可用"""
    # 构造一个只有 画像/ 和 memory_bank/ 可用的伪路径
    fake_vault = os.path.join(os.path.dirname(__file__), "_fake_vault_test")
    fake_profile = os.path.join(fake_vault, "画像", "履历.md")
    fake_bank = os.path.join(fake_vault, ".tool", "lingtai-kb", "memory_bank", "data", "memories.json")

    # 创建临时文件结构
    os.makedirs(os.path.dirname(fake_profile), exist_ok=True)
    os.makedirs(os.path.dirname(fake_bank), exist_ok=True)
    try:
        # 只有画像和银行可用
        with open(fake_profile, "w") as f: f.write("test")
        with open(fake_bank, "w") as f: f.write("{}")
        
        mode = check_mode(fake_vault)
        # 画像 + 银行可用 → L1（丹房不可用）
        assert mode["level"] == "L1"
        assert "丹房" in str(mode["unavailable"])
        assert "hint" in mode
    finally:
        # 清理
        import shutil
        if os.path.exists(fake_vault):
            shutil.rmtree(fake_vault)


def test_l2_when_everything_missing():
    """全不可用 → L2"""
    mode = check_mode(r"C:\nonexistent\path")
    assert mode["level"] == "L2"
    assert "hint" in mode