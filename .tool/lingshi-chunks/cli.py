#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lingshi-chunks CLI — 零台结构化索引命令行工具

用法：
    python cli.py extract <md_path>     从单篇丹房页提取 chunk（规则版）
    python cli.py extract-llm <md_path>   从单篇丹房页提取 chunk（LLM 版）
    python cli.py reindex               全量重建所有丹房页的索引
    python cli.py search <query>        搜索 chunk
    python cli.py stats                 查看索引统计
    python cli.py status                查看索引状态
    python cli.py show <chunk_id>       查看单条 chunk 详情

路径：
    --vault=<path>  指定零台 vault 路径（默认：$LINGTAI_VAULT 或当前目录）
"""

import json
import os
import sys
from pathlib import Path

# 确保能找到 core 模块
sys.path.insert(0, str(Path(__file__).parent))
from core import StructuredIndex


def resolve_vault() -> str:
    """确定 vault 路径。"""
    for arg in sys.argv[1:]:
        if arg.startswith("--vault="):
            return arg.split("=", 1)[1]
    env = os.environ.get("LINGTAI_VAULT", "")
    if env:
        return env
    # 当前目录检测
    cwd = Path.cwd()
    if (cwd / "台律.md").exists() or (cwd / "AGENTS.md").exists():
        return str(cwd)
    return str(cwd)


def find_md(vault_path: str, target: str) -> str:
    """解析目标路径（支持相对路径和标题匹配）。"""
    # 直接是相对路径
    candidate = Path(vault_path) / target
    if candidate.exists() and candidate.suffix == ".md":
        return str(candidate.relative_to(vault_path))

    # 标题匹配：在丹房目录中搜索文件名或标题
    danfang = Path(vault_path) / "丹房"
    if danfang.exists():
        for md in danfang.rglob("*.md"):
            if md.name == target or md.stem == target:
                return str(md.relative_to(vault_path))
            # frontmatter 标题匹配
            if md.name in ("index.md", "README.md"):
                continue
            try:
                text = md.read_text(encoding="utf-8")
                if text.startswith("---"):
                    parts = text.split("---", 2)
                    if len(parts) >= 3:
                        for line in parts[1].strip().split("\n"):
                            if line.startswith("标题:"):
                                title = line.split(":", 1)[1].strip().strip('"').strip("'")
                                if title == target:
                                    return str(md.relative_to(vault_path))
            except Exception:
                continue

    return target  # 原样返回


def cmd_extract(si: StructuredIndex, args: list):
    """lingtai chunks extract <md_path>"""
    if not args:
        print("❌ 请指定丹房页路径或标题")
        return
    target = find_md(si.vault_path, args[0])
    print(f"📄 提取: {target}")
    count = si.extract(target)
    print(f"✅ 完成: {count} chunks")


def cmd_reindex(si: StructuredIndex, args: list):
    """lingtai chunks reindex"""
    print("🏗️  全量重建结构化索引...")
    si.ensure_dirs()
    total = si.reindex_all()
    print(f"\n{'='*40}")
    print(f"  重建完成: {total} chunks")


def cmd_search(si: StructuredIndex, args: list):
    """lingtai chunks search <query>"""
    if not args:
        print("❌ 请指定搜索关键词")
        return
    query = " ".join(args)
    print(f"🔍 搜索: {query}\n")

    results = si.search(query, top_k=10)
    if not results:
        print("  无匹配结果")
        return

    for i, r in enumerate(results, 1):
        print(f"{i:2d}. {r['chunk']}")
        print(f"    域: {r['domain']}  |  分数: {r['score']}")
        print(f"    {r['content'][:120]}...")
        print()


def cmd_stats(si: StructuredIndex, args: list):
    """lingtai chunks stats"""
    stats = si.stats()
    print(f"\n📊 结构化索引统计")
    print(f"{'='*40}")
    print(f"  Schema 版本: {stats['schema_version']}")
    print(f"  chunk 总数: {stats['total_chunks']}")
    print(f"  存储路径: {stats['storage_path']}")
    if stats.get("by_type"):
        print(f"\n  按类型分布:")
        for t, c in sorted(stats["by_type"].items()):
            print(f"    {t:12s}: {c}")
    if stats.get("by_grade"):
        print(f"\n  按品级分布:")
        for g, c in sorted(stats["by_grade"].items()):
            print(f"    {g:6s}: {c}")
    print()


def cmd_status(si: StructuredIndex, args: list):
    """lingtai chunks status"""
    si.ensure_dirs()
    manifest_path = si.index_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        print(f"\n📋 索引状态")
        print(f"{'='*40}")
        print(f"  状态: ✅ 已建立")
        print(f"  版本: {manifest.get('schema_version', '?')}")
        print(f"  chunk 数: {manifest.get('total_chunks', 0)}")
        print(f"  最后更新: {manifest.get('updated_at', '?')}")
    else:
        print(f"\n📋 索引状态")
        print(f"{'='*40}")
        print(f"  状态: 空（尚未提取任何 chunk）")
        print(f"  路径: {si.index_dir}")
    print()


def cmd_show(si: StructuredIndex, args: list):
    """lingtai chunks show <chunk_id>"""
    if not args:
        print("❌ 请指定 chunk_id")
        return
    chunk_id = args[0]
    chunk = si.store.load(chunk_id)
    if not chunk:
        print(f"❌ chunk 不存在: {chunk_id}")
        return
    print(f"\n📄 chunk: {chunk_id}")
    print(f"{'='*40}")
    for key, val in chunk.to_dict().items():
        if key == "content":
            print(f"  content: {str(val)[:300]}")
        elif key == "vector_embedding":
            continue
        else:
            print(f"  {key}: {json.dumps(val, ensure_ascii=False, indent=2)}")
    print()


def cmd_extract_llm(si: StructuredIndex, args: list):
    """lingtai chunks extract-llm <md_path>"""
    if not args:
        print("❌ 请指定丹房页路径或标题")
        return
    target = find_md(si.vault_path, args[0])
    print(f"📄 LLM 提取: {target}")
    count = si.extract_llm(target)
    print(f"✅ 完成: {count} chunks")


def cmd_extract_llm(si: StructuredIndex, args: list):
    """lingtai chunks extract-llm <md_path>"""
    if not args:
        print("? 请指定丹房页路径或标题")
        return
    target = find_md(si.vault_path, args[0])
    print(f"? LLM 提取: {target}")
    count = si.extract_llm(target)
    print(f"? 完成: {count} chunks")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    extra_args = [a for a in sys.argv[2:] if not a.startswith("--vault=")]

    vault = resolve_vault()
    si = StructuredIndex(vault)
    si.ensure_dirs()

    commands = {
        "extract": cmd_extract,
        "extract-llm": cmd_extract_llm,
        "reindex": cmd_reindex,
        "search": cmd_search,
        "stats": cmd_stats,
        "status": cmd_status,
        "show": cmd_show,
    }

    if cmd in ("help", "-h", "--help"):
        print(__doc__)
        return

    func = commands.get(cmd)
    if func:
        func(si, extra_args)
    else:
        print(f"未知命令: {cmd}\n")
        print(__doc__)


if __name__ == "__main__":
    main()