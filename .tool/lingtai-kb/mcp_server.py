# -*- coding: utf-8 -*-
"""
⏸️ DEPRECATED — 已拆分为模块化架构
此文件保留为兼容入口，实际逻辑在 router.py + server.py + server_mixins/
新开发请在对应 server_mixins/<domain>.py 中修改。
MCP 入口不变：此文件仍然是 config.json 指向的 entry point。"""
import sys
from pathlib import Path

# 确保当前目录在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 委托给模块化路由
from router import main

if __name__ == "__main__":
    # 启动标记：写入文件证明新代码已运行
    import os
    _startup_marker = os.path.join(os.path.dirname(__file__), '.startup_marker')
    with open(_startup_marker, 'w') as _f:
        _f.write(f"started at {__import__('datetime').datetime.now()}")
    main()
