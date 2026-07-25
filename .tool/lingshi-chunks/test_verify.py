"""lingshi-chunks 快速验证测试"""
import os, json, tempfile, shutil
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".tool", "lingshi-chunks"))
from core import StructuredIndex

tmp = tempfile.mkdtemp()
vault = os.path.join(tmp, "lingtai-test")
os.makedirs(os.path.join(vault, "丹房", "00-思考与认知"))
os.makedirs(os.path.join(vault, ".lingtai"))

md_content = """---
标题: 递归与迭代的关系
域: 00-思考与认知
品级: 上品
---

## 递归的定义

递归是函数调用自身的方法。包含递归基（终止条件）和递归步（问题分解）。
适合处理树遍历、分治算法等自相似结构。标签：#递归 #编程范式

## 迭代的定义

迭代是重复执行代码直到条件满足。常见形式有 for 和 while 循环。
依赖状态变量维护，适合线性序列。标签：#迭代 #循环

## 转换原则

递归和迭代可互相转换。递归转迭代需显式维护栈；迭代转递归可将循环改为函数调用。
尾递归可兼得两者优势。标签：#代码转换 #尾递归
"""

md_path = os.path.join(vault, "丹房", "00-思考与认知", "递归与迭代的关系.md")
with open(md_path, "w", encoding="utf-8") as f:
    f.write(md_content)

si = StructuredIndex(vault)
si.ensure_dirs()

# 测试提取
count = si.extract(os.path.join("丹房", "00-思考与认知", "递归与迭代的关系.md"))
print(f"提取: {count} chunks")
assert count > 0, "提取失败"

# 测试搜索
results = si.search("递归")
print(f"搜索 '递归': {len(results)} 条")
for r in results:
    print(f"  [{r['domain']}] {r['title']} (score={r['score']})")
    print(f"    {r['content'][:80]}")
assert len(results) > 0, "搜索失败"

results2 = si.search("迭代")
print(f"搜索 '迭代': {len(results2)} 条")
for r in results2:
    print(f"  [{r['domain']}] {r['title']} (score={r['score']})")

# 测试过滤搜索
results3 = si.search("递归", chunk_type="concept")
print(f"按类型过滤 'concept': {len(results3)} 条")

# 测试统计
stats = si.stats()
print(f"统计: {json.dumps(stats, ensure_ascii=False, indent=2)}")
assert stats["total_chunks"] == count, "统计不一致"

shutil.rmtree(tmp)
print("\n✅ 全部测试通过")