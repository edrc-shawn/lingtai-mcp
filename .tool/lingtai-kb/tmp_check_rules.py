# -*- coding: utf-8 -*-
"""检查 skillopt 产出规则中的工具名引用"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from server import LingtaiMCPServer
from decorators import REGISTRY

s = LingtaiMCPServer()
s._ensure_skillopt()
rules = s.skillopt_stager.read_all()
print(f"staged 规则: {len(rules)} 条")
for r in rules[:5]:
    content = r.get("content", "")[:200]
    print(f"  {r.get('filename','?')}: {content}")
    print()

# 检查 product_tools 中的旧名是否需要更新
print("=== product_tools 检查 ===")
old_names = ["perception_save", "mem_write", "user_push"]
new_names = {"perception_save": "knowledge_save", "mem_write": "memory_write", "user_push": "user_push"}
for old in old_names:
    if old in REGISTRY:
        print(f"  {old}: 仍在 REGISTRY 中")
    elif old in _ALIASES if hasattr(s, 'aliases') else None:
        print(f"  {old}: 是别名")
    else:
        target = new_names[old]
        in_reg = "在" if target in REGISTRY else "不在"
        print(f"  {old} → {target}: {in_reg} REGISTRY")