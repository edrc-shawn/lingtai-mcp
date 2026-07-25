# 灵台工具集

灵台运行维护工具脚本。所有路径以 `灵台/` 为相对根。

## 脚本清单

| 脚本 | 用途 | 运行方式 |
|:----|:-----|:--------|
| `scripts/build_index.py` | 构建 JSON 机器索引 | `python .tool/scripts/build_index.py` |
| `scripts/export_for_ai.py` | 构建导出层（quick_ref + 域摘要） | `python .tool/scripts/export_for_ai.py` |
| `scripts/export_profile.py` | 灵识注入 — 写入 IDENTITY.md（灵台状态+用户画像） | `python .tool/scripts/export_profile.py` |
| `scripts/ll_finish.py` | 提炼收尾 CLI（回链→FM→日志→build→git） | `python .tool/scripts/ll_finish.py <原料> <目标> <摘要> <类型>` |
| `scripts/lint_check.py` | 全量体检 | `python .tool/scripts/lint_check.py` |
| `scripts/semantic_scan.py` | 语义关联扫描 | `python .tool/scripts/semantic_scan.py` |
| `scripts/find_shortest_pending.py` | 找最短待提炼原料 | `python .tool/scripts/find_shortest_pending.py` |
| `scripts/log_draft.py` | 从 git log 生成日志草稿 | `python .tool/scripts/log_draft.py` |
| `scripts/backlink_adder.py` | 反链补全 | `python .tool/scripts/backlink_adder.py` |
| 其他 | 辅助/修复脚本 | 按需查看 |

## 约定

- CWD 应为仓库根（`灵台/`）
- 所有脚本 UTF-8 编码
- `.meta/` 下的 `.mtimes.json` 不进版本控制

## 灵识知识引擎

`lingshi/` 目录包含基于 `index.json` 的知识查询引擎，不维护独立数据库：

| 模块 | 功能 | 调用方式 |
|:----|:-----|:--------|
| `memory_engine.py` | 查询 + 图扩散搜索 | 被 `lingtai_integration.py` 调用 |
| `auto_edge.py` | 链接分析与关联发现 | 同上 |
| `reasoning_engine.py` | 推理分析 | 同上 |
| `token_monitor.py` | Token 消耗追踪 | 独立 |
| `lingtai_integration.py` | CLI 入口 | `python .tool/lingshi/lingtai_integration.py <命令>` |

支持命令：`query` / `analyze` / `relation` / `links` / `stats` / `search`
