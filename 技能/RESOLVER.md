# 灵台工具路由表

> 灵识的工具选择指南。**每次会话启动时读一次**，之后按此表路由。
> 如果两个工具都能匹配，读 Disambiguation Rules 再选。

### TL;DR（一句话版）

| 用户想 | 做什么 |
|--------|--------|
| 问知识 | 读 `技能/search.skill.md` → 优先 `knowledge_synthesize` |
| 记/查记忆 | 读 `技能/memory.skill.md` → `memory_write` / `memory_search` |
| 保存/提炼 | 读 `技能/refine.skill.md` → `raw_save` → `refine_quick` |
| 体检 | 读 `技能/health.skill.md` → `health_inspect` |
| 收尾 | 读 `技能/system.skill.md` → `session_end` |
| 不确定 | `lingshi_classify(question=<原话>)` 拿推荐 |

---

## 常驻（每消息必做）

| 触发条件 | 做什么 |
|---------|--------|
| 每收到用户消息 | 并行调 `detect_memory_signal(text=<用户原话>)` — 检测有无记忆信号 |
| 需要用户画像/记忆上下文 | `lingshi_inject(keyword=<会话主题>)` — 规则⑲要求 |
| 不确定用户想干什么 | `lingshi_classify(question=<用户原话>)` — 拿分类推荐（覆盖 8 类场景） |

---

## 知识操作

| 用户意图信号 | 怎么做 | 优先级 |
|------------|-------|--------|
| "什么是X""解释X""为什么X""X和Y的区别" | **`knowledge_synthesize`** — 检索+合成回答+差距分析，一步到位 | P0 |
| 知道关键词，想自己看原始页面 | **`knowledge_search`** — 返回页面列表，自己判断 | P1 |
| 不确定关键词，想"逛"知识图谱 | **`knowledge_explore`** — 多跳关联+跨域探索 | P1 |
| 搜原料/技能/作品的非丹房文件 | **`fulltext_search`** — 搜技能/原料/作品/外部参考 | P1 |
| 知识库四层全空，需要联网 | **`web_search`** — 最后回退，knowledge_search 已含联网层 | P2 |
| 把相关知识注入到对话窗口 | **`knowledge_inject`** — 有 max_tokens 上限，按预算注入 | P2 |
| 问题模糊，先消解方向 | **`question_dissolve`** — 消解漏斗，返回多个理解方向

### 区别规则

```
knowledge_synthesize vs knowledge_search:
  → 要"答案"用 synthesize，要"页面列表"用 search

knowledge_search vs fulltext_search:
  → search 只搜丹房页，fulltext_search 搜原料/技能/作品

knowledge_search vs web_search:
  → search 已含联网层作为第 4 回退，通常不需要单独调 web_search
```

---

## 记忆操作

| 用户意图信号 | 怎么做 | 优先级 |
|------------|-------|--------|
| "记住X""以后注意X""我偏好X" | `detect_memory_signal(text=<原话>)` → `memory_write` | P0 |
| "之前发生过什么""上次怎么决定的" | **`memory_search`** — 搜记忆银行 | P0 |
| "最近几天做了什么" | **`episodic_search(days=7)`** — 查看近期会话摘要（已合并 episodic_recent） | P1 |
| "某次对话的具体操作细节" | **`episodic_search`** — 按关键词搜历史会话 | P1 |
| 一次性注入全部记忆层 | **`lingshi_inject`** — 画像+教训+记忆+系统四层 | P1 |
| 记忆→知识毕业检查 | **`memory_consolidate`** — 只读扫描，标记毕业候选 | P2 |

### 区别规则

```
memory_search vs episodic_search vs knowledge_search:
  → memory_search = 找教训/偏好/纠正（记忆银行）
  → episodic_search = 找历史会话操作细节（L1 情景记忆）
  → knowledge_search = 找知识概念（丹房页）

memory_search vs lingshi_inject:
  → inject 一次性注入全部 4 层（适合启动时）
  → search 按关键词精准检索（适合会话中）
```

---

## 提炼操作

| 用户意图信号 | 怎么做 | 优先级 |
|------------|-------|--------|
| "提炼这个" | 先走完整提炼流程（选料→三检→写页→补链→收尾） | P0 |
| 保存一段未加工素材 | **`raw_save`** — 存到原料/目录，待提炼 | P0 |
| 创建成品知识页 | **`page_create`** — 存到丹房/目录，即时可检索 | P0 |
| 修改已有页 | **`page_update`**（整页）或 **`page_append_section`**（章节级） | P0 |
| 提炼收尾登记 | **`refine_mark`** — 更新原料 FM + 追加日志 | P0 |
| 一步完成提炼全流程 | **`refine_quick`** — 预检→写页→标记→建链 | P0（优先） |
| 查提炼状态 | **`refine_status`** — 单篇/某页来源/全量统计 | P1 |
| 批量扫描原料 | **`raw_derive`** — 零 LLM 推导原料元数据 | P1 |
| 看哪些原料没提炼 | **`knowledge_gaps`** — 扫描待提炼原料 | P1 |

### 区别规则

```
raw_save vs page_create:
  → raw_save = 存未加工素材（原料/）
  → page_create = 存成品知识页（丹房/）

page_update vs page_append_section:
  → update = 整页级追加或替换
  → append_section = 只在某个 ## 章节下精准插入

refine_quick vs 手动分步:
  → 优先用 refine_quick，一步完成
  → 只有在 refine_quick 不满足需求时才分步调
```

---

## 体检/分析操作

| 用户意图信号 | 怎么做 | 优先级 |
|------------|-------|--------|
| "看看知识库怎么样""健康检查" | **`health_inspect`** — 全量体检汇总 | P0 |
| "知识库有什么缺口" | **`knowledge_gaps`** — 扫描待提炼 | P1 |
| "哪些页面最热" | **`knowledge_heatmap`** — 查询/引用活跃度 | P1 |
| "反思一下最近的工作" | **`observation_reflect`** — 全量反思五检 | P1 |
| "哪些知识可以清理了" | **`lifecycle_scan`** — 可降级/清理的页面 | P2 |
| "跨域有没有意外关联" | **`concept_collide`** — 语义相似度检测 | P2 |
| 标记缺口状态 | **`health_ledger`** — 读/写对账面板 | P2 |

### 区别规则

```
health_inspect vs 子工具:
  → inspect = 聚合报告（包含缺口+热度+反思+对账）
  → 只看单一维度用子工具（gaps/heatmap/reflect/ledger）
```

---

## 系统操作

| 用户意图信号 | 怎么做 | 优先级 |
|------------|-------|--------|
| 会话结束 | **`session_end`** — 收尾宏（5 步一次跑完） | P0（必做！） |
| 重建索引 | **`system_refresh_index`** — 增量重建 | P0（提 Git 后做） |
| "这个工具怎么用" | **`system_sop`** — 查工具 SOP | P1 |
| 重新加载 MCP | **`sys_reload`** — 热重载（不改代码不用调） | P2 |

---

## Disambiguation Rules（路由冲突时查这里）

当多个工具都能匹配时：

1. **优先用宏** — `refine_quick` > 分步调子工具；`session_end` > 手动收尾；`health_inspect` > 子体检
2. **优先用合成** — `knowledge_synthesize` > `knowledge_search`（除非你要原始页面）
3. **优先用记忆信号** — 用户说"记住"时先调 `detect_memory_signal`，再决定写哪里
4. **优先查知识库** — 任何外部查询前，先 `knowledge_search` 或 `knowledge_synthesize`（brain-first 原则）
5. **不确定时** — 调 `lingshi_classify` 拿路由推荐，不要自己猜
6. **写了内容后** — 必须 `refine_mark`（提炼）或 `system_refresh_index`（建索引）或 `session_end`（收尾）

---

## 惯例文件（跨所有操作）

以下文件定义了跨工具的统一规则，读对应技能前先读惯例：

| 文件 | 什么事 |
|------|--------|
| `技能/惯例/工具选择.md` | 相似工具之间怎么选（对应上文的区别规则） |
| `技能/惯例/提炼流程.md` | 完整提炼管线：选料→三检→写页→补链→收尾 |
| `技能/惯例/收尾流程.md` | 会话结束流水线：写日志→建索引→提 Git→收尾 |
| `技能/惯例/记忆信号.md` | 用户说"记住"时，怎么判断写哪里 |

## Skill 文件（高频流程封装）

复杂操作流程已封装为 skill 文件，读文件 → 按步骤执行：

| Skill 文件 | 覆盖场景 | 涉及的子工具 |
|------------|---------|-------------|
| `技能/search.skill.md` | 知识检索、搜索、对比分析 | knowledge_explore, inject, gaps, heatmap, compound, stats |
| `技能/refine.skill.md` | 保存素材、提炼、波及检查、质检 | raw_derive, ingest_ripple, refine_status, page_add_link, nightly_enrich |
| `技能/health.skill.md` | 健康检查、缺口发现、清理建议 | lifecycle_scan, observation_reflect, quality_check, stats |
| `技能/memory.skill.md` | 记记忆、查记忆、管理记忆银行 | memory_feedback, link, consolidate, snapshot, restore |
| `技能/system.skill.md` | 会话收尾、索引重建、提 Git | sys_reload, skillopt_*, output_*, agent_*, user_* |

> 子工具不需要记，skill 文件里会告诉你什么时候该用哪个。