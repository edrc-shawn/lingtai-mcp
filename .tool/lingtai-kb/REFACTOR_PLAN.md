# 灵台 MCP 重构计划

> 审计日期：2026-07-16 | 目标：84 工具 → 42 工具，消除双注册，建立中间件管道

---

## 一、现状基线

| 指标 | 数值 |
|------|------|
| `_TOOL_MAP` 注册 | 84 个 |
| tools.py 暴露 | 35 个 |
| 隐藏但可调用 | 49 个 |
| 别名映射 | 33 条 |
| Python 总行数 | ~19,000 |
| 最大单文件 | perception.py (1711行) |
| 日均调用 | ~30 次 |

---

## 二、9 组合并动作

### M1: 知识概览合并
`knowledge_stats` + `knowledge_domains` + `knowledge_pages` → **`knowledge_overview`**

```yaml
knowledge_overview:
  mode: "stats" | "domains" | "pages"   # 默认 "stats"
  domain: "00-思考与认知"                # mode=pages 时筛选
  limit: 50
```

### M2: 证据链吸收
`knowledge_search_evidence` → **`knowledge_search`** 加参数

```yaml
knowledge_search:
  evidence: true   # 新增：返回锚点权重/评分/匹配原因
```

### M3: 提炼状态合并
`refine_all_status` + `refine_list_sources` → **`refine_status`** 加 mode

```yaml
refine_status:
  mode: "single" | "all" | "sources"   # all=全量统计, sources=查某页的原料
  raw_path: "原料/xxx.md"              # mode=single 时
  target: "丹房/..."                   # mode=sources 时
  domain: ""                           # mode=all 时按域筛选
```

### M4: 原料推导合并
`raw_derive_batch` → **`raw_derive`** 加 mode

```yaml
raw_derive:
  mode: "single" | "batch"   # batch=批量扫描
  raw_path: "原料/xxx.md"    # mode=single 时
  limit: 50                  # mode=batch 时
  skip_refined: true
```

### M5: 记忆生命周期合并
`memory_lifecycle` → **`memory_stats`** 的子字段

`memory_stats` 返回增加 `lifecycle` 字段（毕业延迟 + 反向泄漏率），不再需要单独工具。

### M6: 记忆运维合并
`memory_merge` + `memory_archive` → **`memory_feedback`** 扩展 action

```yaml
memory_feedback:
  action: "adopt" | "reject" | "merge" | "archive"
  memory_id: "mem-xxx"
  target_branch: "通用"     # action=merge 时
  reason: "obsolete"        # action=archive 时
```

### M7: 观察面板合并
`observation_list` + `observation_stats` + `observation_rule_health` → **`observation_dashboard`**

```yaml
observation_dashboard:
  mode: "summary" | "list" | "rules"
  keyword: ""        # mode=list 时搜索
  limit: 20
```

### M8: 体检面板合并
`system_health` + `system_check_status` → **`health_inspect`** 吸收

`health_inspect` 返回增加 `git_changes` + `recent_operations` 字段（原 `system_check_status` 内容），`system_health` 的指标合并进去。`health_ledger` 保留独立（每日检管线专用）。

### M9: 日志搜索合并
`system_search_logs` → **`fulltext_search`** 加 scope

```yaml
fulltext_search:
  scope: "技能" | "原料" | "作品" | "外部参考" | "日志" | "all"
```

---

## 三、最终工具清单（42 个）

### 🏛️ 知识检索（6）
| 工具 | 说明 | 变更 |
|------|------|------|
| `knowledge_search` | 丹房搜索 + 证据链 | M2：加 `evidence` 参数 |
| `knowledge_explore` | 图探索/关联 | 不变 |
| `knowledge_inject` | 注入丹房页 | 不变 |
| `knowledge_recall` | 宏：inject+search | 不变 |
| `knowledge_digest` | 消化建议 | 不变 |
| `knowledge_overview` | 统计+域+页列表 | **新** M1 合并 |

### 📄 知识管理（8）
| 工具 | 说明 | 变更 |
|------|------|------|
| `knowledge_save` → `raw_save` | 保存知识→存原料 | P2: 改名消除"知识"误导，实为存原料 |
| `page_create` | 创建丹房页 | 不变 |
| `page_update` | 更新丹房页 | 不变 |
| `page_append_section` | 精准追加 | 不变 |
| `page_read` | 读取丹房页 | 不变 |
| `page_add_link` | 建立链接 | 不变 |
| `page_link_suggest` | 链接建议 | 🟢 暴露 |
| `page_history` | 版本追溯 | 🟢 暴露 |

### 🔬 提炼管线（4）
| 工具 | 说明 | 变更 |
|------|------|------|
| `refine_quick` | 宏：一键提炼 | 不变 |
| `refine_mark` | 提炼登记 | 不变 |
| `refine_status` | 提炼状态（单/全/来源） | M3：吸收 all_status + list_sources |
| `raw_derive` | 原料推导（单/批量） | M4：吸收 batch |

### 🧠 记忆系统（8）
| 工具 | 说明 | 变更 |
|------|------|------|
| `memory_write` | 写入记忆 | 不变 |
| `memory_search` | 搜索记忆 | 不变 |
| `memory_stats` | 统计+生命周期 | M5：吸收 lifecycle |
| `memory_consolidate` | 毕业建议 | 不变 |
| `memory_feedback` | 反馈/合并/归档 | M6：吸收 merge + archive |
| `memory_link` | 记忆→知识桥 | 🟢 暴露 |
| `memory_scan_conflicts` | 冲突扫描 | 🟢 暴露（每周查用） |
| `memory_decay` | 遗忘归档管线 | 🟡 管线专用，不加 `_system` 前缀但 hidden |

### 🔍 观察反思（2）
| 工具 | 说明 | 变更 |
|------|------|------|
| `observation_dashboard` | 观察总览 | **新** M7 合并 |
| `observation_reflect` | 全量反思五检 | 🟢 暴露 |

### 🩺 体检系统（4）
| 工具 | 说明 | 变更 |
|------|------|------|
| `health_inspect` | 全量体检 | M8：吸收 system_health + check_status |
| `health_ledger` | 对账面板 | 🟢 暴露（每日检管线用） |
| `knowledge_gaps` | 知识缺口 | 不变 |
| `knowledge_heatmap` | 热度图 | 🟢 暴露 |

### 🧩 灵识注入（3）
| 工具 | 说明 | 变更 |
|------|------|------|
| `lingshi_inject` | 4层记忆注入 | 不变 |
| `user_push` | 推送偏好 | 🟢 暴露 |
| `user_feedback` | 纠正反馈 | 🟢 暴露 |

### 🔧 系统运维（7）
| 工具 | 说明 | 变更 |
|------|------|------|
| `context_load` | 会话上下文 | 不变 |
| `session_end` | 宏：收尾五步 | 不变 |
| `system_refresh_index` | 重建索引 | 不变 |
| `sys_reload` | 热重载 | ⚠️ dev-only 标记 |
| `fulltext_search` | 全资产搜索 | M9：加 scope="日志" |
| `web_search` | 联网搜索 | 不变 |
| `cross_end_activity` | 跨端活动 | 不变 |
| `system_sop` | 工具指南 | 不变 |

---

## 四、删除清单（7 个）

| 工具 | 理由 |
|------|------|
| `domain_visibility` | 无权限系统，始终返回全可访问 |
| `vector_index_status` | 查旧索引，语义检索已迁 memory_bank |
| `skill_list` | 与 `agent_skills` 重叠，且 agent_* 体系无人用 |
| `agent_recommend` | 433行代码，0次实际调用 |
| `agent_feedback` | 同上 |
| `agent_skills` | 同上 |
| `system_registry_scan` | 功能已被 `knowledge_overview` 覆盖 |

> ⚠️ agent_* 体系如需保留延后决策，暂不删代码只删工具注册

**代码删除量估算：** agent_recommender.py(433行) + 各 mixin 中对应方法(~200行) = ~600 行

---

## 五、管线工具（8 个，不加 `_system` 前缀，保持 hidden）

| 工具 | 用途 |
|------|------|
| `skillopt_dryrun` | 睡眠进化预览 |
| `skillopt_run` | 睡眠进化执行 |
| `skillopt_status` | 查看 staged 规则 |
| `skillopt_adopt` | 采纳规则 |
| `skillopt_reject` | 拒绝规则 |
| `skillopt_log` | 进化历史 |
| `auto_evolve` | 知识自动演化 |
| `system_token` | Token 监控 |
| `get_macro_stats` | 宏使用率统计 |
| `system_restart` | 热重启（危险操作） |

---

## 六、需决策（4 个）

| 工具 | 说明 | 建议 |
|------|------|------|
| `topic_match` | 热点匹配 | 保留暴露（选题场景偶尔用） |
| `output_list` | 列出作品 | 保留暴露 |
| `output_publish` | 发布作品 | 保留暴露 |
| `episodic_search` | 情景搜索 | 保留暴露 |

---

## 七、别名清理

保留 5 条核心别名（AI prompt 模板写死了这些），其余 28 条删除：

| 别名 | → 规范名 | 保留理由 |
|------|---------|---------|
| `kb_query` | `knowledge_search` | 大量 prompt 模板硬编码 |
| `kb_search` | `knowledge_search` | 同上 |
| `mem_write` | `memory_write` | 同上 |
| `mem_query` | `memory_search` | 同上 |
| `sys_refresh_index` | `system_refresh_index` | 同上 |

---

## 八、架构优化（并行进行）

### 8.1 装饰器统一注册（替换 tools.py + router.py 双注册）

```python
# 新增 decorators.py（~60行）
from functools import wraps
import inspect

_REGISTRY = {}

def tool(*, readonly=False, category="general"):
    def deco(fn):
        sig = inspect.signature(fn)
        # 自动从类型注解 + docstring 生成 JSON Schema
        schema = _build_schema(fn, sig)
        _REGISTRY[fn.__name__] = {
            "fn": fn, "readonly": readonly,
            "category": category, "schema": schema,
        }
        @wraps(fn)
        def wrapper(self, **kwargs):
            return fn(self, **kwargs)
        return wrapper
    return deco
```

改动范围：
- 新增 `decorators.py`
- 修改所有 mixin 的方法加 `@tool(...)` 装饰器
- 删除 `tools.py`
- 简化 `router.py`（`_TOOL_MAP` 由装饰器注册表自省构建）

### 8.2 中间件管道

```python
# router.py handle_request 从 70 行降到 ~15 行
def handle_request(request):
    return pipeline(
        parse_mcp_method,
        resolve_alias,
        lazy_context,       # 懒加载上下文
        validate_args,      # 新：参数校验
        execute_handler,
        log_session,        # 会话记录
        wrap_response,      # 统一 ok/fail 包装
    )(request)
```

### 8.3 拆分 perception.py

| 新文件 | 内容 | 行数 |
|--------|------|------|
| `server_mixins/page_manager.py` | create_page/update_page/append_section/read_page/add_link/link_suggest | ~600 |
| `server_mixins/refine.py` | refine_mark/refine_status/_refine_map_* | ~400 |
| `server_mixins/perception.py` | inject/save/lingshi_inject | ~500 |

---

## 九、执行顺序

```
Phase 1 (地基) ✅ DONE 2026-07-16
  ├── ✅ 1.1 装饰器系统 — decorators.py + 84 方法全部 @tool()
  ├── ✅ 1.2 中间件管道 — router.py 管道化 + 自动构建 _TOOL_MAP
  ├── ✅ 验证: --test 73 tools, initialize/tools/list 冒烟通过
  └── ⏳ 1.3 需要重启 MCP 进程让新代码生效

Phase 2 (合并)
  ├── 2.1 M1~M9 逐一合并
  ├── 2.2 别名清理（保留5条）
  └── 每步 sys_reload 验证

Phase 3 (暴露)
  ├── 3.1 27 个 🟢 工具取消注释 → tools.py（已废弃，改为装饰器注册）
  ├── 3.2 4 个需决策工具确认
  └── 4 个 🟡 管线工具保持 hidden

Phase 4 (删除)
  ├── 4.1 删除 7 个冗余工具注册 + 实现
  ├── 4.2 拆分 perception.py
  └── 4.3 按需加载引擎

Phase 5 (文档)
  ├── 5.1 更新 AGENTS.md 工具偏好表
  ├── 5.2 更新 AGENTS-appendix.md 工具速查表
  └── 5.3 更新 system_sop 覆盖全部 42 工具
```

---

## 十、影响面

| 维度 | 重构前 | 重构后 |
|------|--------|--------|
| 工具总数 | 84 | **42** (-50%) |
| 暴露工具 | 35 | **42** (+7，合并后净增) |
| 隐藏工具 | 49 | **0**（管线工具归入 `_system_*` 命名，不双注册） |
| 别名 | 33 | **5** |
| 最大文件 | 1711行 | **600行** |
| 工具注册文件 | 2 | **0**（装饰器自省） |
| 代码删除 | — | **~2,500 行** |
| 中间件逻辑 | 70行内联 | **~80行 管道** |
