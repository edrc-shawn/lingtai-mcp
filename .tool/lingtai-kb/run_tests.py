# -*- coding: utf-8 -*-
"""灵台 MCP Server 测试运行器——不依赖 pytest 的包发现"""
import sys
import os

# 添加 lingtai-kb 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# 收集测试
test_dir = os.path.join(os.path.dirname(__file__), "tests")
test_files = sorted(f for f in os.listdir(test_dir) if f.startswith("test_") and f.endswith(".py"))

passed = 0
failed = 0
errors = []

for tf in test_files:
    test_path = os.path.join(test_dir, tf)
    print(f"\n=== {tf} ===")
    
    # 在独立的命名空间中执行测试文件
    ns = {"__file__": test_path}
    exec(open(test_path, encoding="utf-8").read(), ns)
    
    # 收集 test_ 开头的函数
    tests = [(k, v) for k, v in ns.items() if k.startswith("test_") and callable(v)]
    
    for name, func in tests:
        try:
            func()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            failed += 1
            errors.append(f"{tf}::{name}: {e}")
        except Exception as e:
            print(f"  💥 {name}: {e}")
            failed += 1
            errors.append(f"{tf}::{name}: {e}")

print(f"\n{'='*40}")
print(f"结果: {passed} passed, {failed} failed")
if errors:
    print("\n失败明细:")
    for e in errors:
        print(f"  - {e}")

sys.exit(1 if failed else 0)
