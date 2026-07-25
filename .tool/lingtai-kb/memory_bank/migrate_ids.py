"""
记忆银行 ID 迁移脚本：md5[:8] → sha256[:12]

用法：
    python .tool/lingtai-kb/memory_bank/migrate_ids.py          # 预览（不写）
    python .tool/lingtai-kb/memory_bank/migrate_ids.py --apply  # 执行迁移
"""

import json
import sys
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from content_registry import mem_id_from_hash, ContentRegistry


def migrate(dry_run: bool = True) -> dict:
    data_dir = Path(__file__).parent / "data"
    memories_path = data_dir / "memories.json"

    if not memories_path.exists():
        return {"error": f"记忆库文件不存在: {memories_path}"}

    with open(memories_path, "r", encoding="utf-8") as f:
        memories = json.load(f)

    stats = {
        "total": len(memories),
        "old_id_count": 0,
        "new_id_count": 0,
        "merged": 0,
        "id_changes": [],
        "dup_removed": [],
    }

    # 第一遍：计算新 ID，检测冲突
    new_map = {}  # new_id → [old_idx, ...]
    for idx, m in enumerate(memories):
        content = m.get("content", "")
        if not content:
            continue
        old_id = m.get("id", "")
        new_id = mem_id_from_hash(content)

        if old_id == new_id:
            stats["new_id_count"] += 1
        else:
            stats["old_id_count"] += 1
            stats["id_changes"].append({
                "old_id": old_id,
                "new_id": new_id,
                "content_preview": content[:60],
            })

        if new_id not in new_map:
            new_map[new_id] = []
        new_map[new_id].append(idx)

    # 第二遍：处理冲突（相同内容的不同记忆 → 合并置信度）
    merged_count = 0
    for new_id, indices in new_map.items():
        if len(indices) > 1:
            # 多条记忆对应新 ID → 保留置信度最高的一条
            best_idx = max(indices, key=lambda i: memories[i].get("current_confidence", 0))
            deprecate_indices = [i for i in indices if i != best_idx]

            for di in deprecate_indices:
                old_id = memories[di].get("id", "")
                memories[best_idx]["evidence_count"] = max(
                    memories[best_idx].get("evidence_count", 0),
                    memories[di].get("evidence_count", 0),
                )
                # 合并标签
                old_tags = set(memories[di].get("tags", []))
                new_tags = set(memories[best_idx].get("tags", []))
                memories[best_idx]["tags"] = list(new_tags | old_tags)
                # 标记为 deprecated
                memories[di]["id"] = new_id + "_old"
                memories[di]["status"] = "deprecated"
                stats["dup_removed"].append({
                    "kept": old_id,
                    "deprecated": memories[di].get("id", ""),
                })
                merged_count += 1

    stats["merged"] = merged_count

    # 第三遍：更新 ID
    for idx, m in enumerate(memories):
        content = m.get("content", "")
        if not content:
            continue
        old_id = m.get("id", "")
        new_id = mem_id_from_hash(content)
        if old_id != new_id and not old_id.endswith("_old"):
            m["id"] = new_id

    # 第四遍：注册到内容注册表
    registry = ContentRegistry()
    reg_stats = {"registered": 0, "skipped": 0}
    for m in memories:
        content = m.get("content", "")
        mem_id = m.get("id", "")
        if not content or mem_id.endswith("_old"):
            continue
        existing = registry.lookup(content)
        if not existing:
            registry.register(content, location=mem_id, module="memory_bank", content_type="memory")
            reg_stats["registered"] += 1
        else:
            reg_stats["skipped"] += 1

    if dry_run:
        print(f"=== 预览模式（加 --apply 执行）===")
        print(f"总记忆数: {stats['total']}")
        print(f"旧格式 ID (md5[:8]): {stats['old_id_count']}")
        print(f"新格式 ID (sha256[:12]): {stats['new_id_count']}")
        print(f"重复合并: {stats['merged']} 条")
        print(f"注册表新增: {reg_stats['registered']}")
        if stats["id_changes"]:
            print(f"\nID 变更示例（前 5 条）:")
            for c in stats["id_changes"][:5]:
                print(f"  {c['old_id']} → {c['new_id']}  ({c['content_preview'][:40]}...)")
        if stats["dup_removed"]:
            print(f"\n重复合并示例（前 3 条）:")
            for d in stats["dup_removed"][:3]:
                print(f"  保留: {d['kept']}  →  废弃: {d['deprecated']}")
    else:
        # 写回
        with open(memories_path, "w", encoding="utf-8") as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        print(f"=== 迁移完成 ===")
        print(f"总记忆数: {stats['total']}")
        print(f"旧→新 ID: {stats['old_id_count']} 条")
        print(f"重复合并: {stats['merged']} 条")
        print(f"注册表新增: {reg_stats['registered']} 条")

    return stats


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    migrate(dry_run=dry_run)
