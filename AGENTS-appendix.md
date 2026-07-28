---
标题: 灵台项目规则 — 附录
日期: 2026-07-12
---

# AGENTS 附录（按需加载）

本文件是 `AGENTS.md` 的按需补充。不需要每会话注入，AI 在需要时自行查阅。

---

## 工具速查表

| 工具 | 归属规则 | 成本 | 说明 | 支柱 |
|------|---------|------|------|------|
| `knowledge_inject` | 规则① | 免费 | 知识/概念类问题注入（v6：未命中自动 fulltext_search 补盲） | 🏛️ LLM Wiki |
| `knowledge_save` | 规则②⑥ | 免费 | 保存知识 + 触发观察 | 🏛️ LLM Wiki |
| `knowledge_recall` | 规则①⑤ | 免费 | **宏工具** inject + search 一步完成 | 🏛️ LLM Wiki |
| `knowledge_search` | 规则⑤ | 免费 | 丹房页搜索（四层 fallback：丹房→原料→联网→全文搜索） | 🏛️ LLM Wiki |
| `knowledge_overview` | M1 | 免费 | **新** 知识库概览（mode=stats/domains/pages，替代 knowledge_stats/domains/pages） | 🏛️ LLM Wiki |
| `fulltext_search` | 规则⑤ | 免费 | 补盲搜索（技能/原料/作品/日志/all）M9: 吸收 system_search_logs | 🏛️ LLM Wiki |
| `context_load` | 规则④ | 免费 | **会话启动必调**。返回画像三层+教训+约束集+工作印记 | 🏛️ LLM Wiki |
| `lingshi_classify` | 规则① | 免费 | 问题分类路由 — 不确定分类时调此工具自动分流 | 🧠 灵识 |
| `lingshi_inject`（已废弃→`context_load`） | 记忆/教训 | 免费 | 灵识4层统一注入（记忆/教训/习惯类问题用，已由 `context_load` 替代） | 🧠 灵识 |
| `user_push` | 规则⑦ | 免费 | 推偏好/习惯到画像 | 🧠 灵识 |
| `memory_write` | 规则⑧⑪ | 免费 | 写入经验记忆。用于教训/偏好/可泛化行为 | 🧠 灵识 |
| `memory_search` | 规则⑧ | 免费 | 搜索记忆银行（支持 branch 过滤 + 语义检索）| 🧠 灵识 |
| `memory_feedback` | 规则⑧ | 免费 | 记忆操作（adopt/reject/merge/archive）M6: 吸收 memory_merge+archive | 🧠 灵识 |
| `memory_stats` | 规则⑧ | 免费 | 记忆银行统计 M5: 含 lifecycle 子字段 | 🧠 灵识 |
| `memory_consolidate` | 规则⑧ | 免费 | 记忆→知识毕业建议 | 🧠 灵识 |
| `memory_link` | 规则⑧ | 免费 | 记忆→知识 wikilink 桥 | 🧠 灵识 |
| `user_feedback` | 规则⑨ | 免费 | ⚠️ 别与 memory_feedback 搞混。纠正/确认画像 | 🧠 灵识 |
| `session_end` | §5.8 | 免费 | **宏工具** 收尾批量处理五步合一 | 🔧 引擎 |
| `ingest_ripple` | §三④ | 免费 | 波及分析预览，AI 审查后逐条 `page_append_section` | 🏛️ LLM Wiki |
| `sys_reload` | 改代码后 | 免费 | 热重载 MCP，不中断会话 | 🔧 引擎 |
| `system_refresh_index` | 辅助 | 免费 | 重建丹房索引 | 🔧 引擎 |
| `health_inspect` | 🔍 体检 | 免费 | 全量体检汇总 M8: 吸收 system_health + git_status | 🔍 四横刀 |
| `health_ledger` | 🔍 体检 | 免费 | 对账面板读写 | 🔍 四横刀 |
| `observation_dashboard` | M7 | 免费 | **新** 观察总览（stats+rules 一步获取） | 🧠 灵识 |
| `observation_reflect` | 规则⑤ | 免费 | 全量反思五检 | 🔍 四横刀 |
| `refine_quick` | 提炼 | 免费 | **宏工具** 快速提炼 | 🏛️ LLM Wiki |
| `refine_status` | 提炼 | 免费 | 提炼状态 M3: mode=single/all/sources | 🏛️ LLM Wiki |
| `raw_derive` | 提炼 | 免费 | 原料推导 M4: mode=single/batch | 🏛️ LLM Wiki |
| `page_create` / `page_update` / `page_read` / `page_add_link` / `page_append_section` | — | 免费 | 丹房页 CRUD | 🏛️ LLM Wiki |
| `page_link_suggest` / `page_history` | — | 免费 | 链接建议 / 版本追溯 | 🔍 四横刀 |
| `knowledge_heatmap` / `knowledge_compound` / `knowledge_gaps` | — | 免费 | 热度图 / 共现权重 / 知识缺口 | 🔍 四横刀 |
| `concept_collide` / `lifecycle_scan` | — | 免费 | 概念碰撞 / 生命周期扫描 | 🔍 四横刀 |
| `topic_match` / `episodic_recent` / `episodic_search` | — | 免费 | 热点匹配 / 情景记忆 | 🔍 四横刀 |
| `output_list` / `output_publish` | — | 免费 | 作品管理 | 📤 输出 |

可选协议：
- `共识协议.md`：跨域复杂问题走三通道 search+graph+tavily 交叉对比
- `子代理验证协议.md`：质疑结论时启独立子代理逐条验证

### 工具偏好（尽快养成，从主文件迁入）

| 场景 | 工具 | 替代旧习惯 |
|------|------|-----------|
| 健康检查（必用） | `health_inspect` | 替代逐个调多个健康工具（M8: 已吸收 system_health + git_status）。**查系统状态第一步必调** |
| 知识库概览 | `knowledge_overview` | 替代 knowledge_stats/domains/pages（M1 合并） |
| 知识合成（必用） | `knowledge_synthesize` | **分析类问题第一步必调**。返回合成正文+差距标注+**问题前置澄清**（自动检测歧义/多义/隐含假设）+**延伸方向建议**（suggested_next: page/tool 推荐，借鉴 dbskill Skill 路由） |
| 观察总览 | `observation_dashboard` | 替代 observation_stats/rule_health（M7 合并） |
| 记忆类问题（必用） | `context_load` | **每会话必调**。替代手写 `lingshi_inject` + 人工筛选 |
| 记忆健康检查 | `memory_stats` + `memory_consolidate` | 每周查一次：活跃数/pending数/毕业率/平均置信度 |
| 跨会话存档（必用） | `memory_snapshot` + `memory_restore` | **每会话收尾前必调**。把关键结论/已否决方向/待解问题存快照，下次 `memory_restore` 接续 |
| 发现热门/冷门页 | `knowledge_heatmap` | 每周查时可跑一次，指导补什么 |
| 发现隐式关联 | `knowledge_compound` | 每周查时跑 top_n 看跨域连接 |
| 跨域碰撞 | `concept_collide` | 发现跨域意外关联（0.6-0.75相似度区间），每周可跑一次 |
| 搜非丹房内容 | `fulltext_search` | `knowledge_search` 只搜丹房，此工具补盲原料/技能/作品/日志 |
| 推荐外部工具 | `external_tool_recommend` | 丹房无法回答时，扫描 337 个外部 SKILL.md 推荐最匹配的 Skill/工具 |
| 回顾近期会话 | `episodic_recent` | 新会话开头跑一次，了解上周脉络 |
| 提炼状态 | `refine_status` | mode=single/all/sources 一站式（M3 合并） |
| 原料推导 | `raw_derive` | mode=single/batch 一站式（M4 合并） |
| 丹房去重检测 | `dedup_check.py` | 三层检测（body_hash/标题/wikilink），建新页或定期查碎片化 |

### 提炼选料细则（从主文件迁入）

**分级辅助**（`raw_derive` 自动打分，`sort_by="newest"` 默认新料优先，`sort_by="oldest"` 回溯旧料）：

| 分级 | 标准 | 分流 |
|------|------|------|
| 快速 | 短文本/模糊开头/无事实 | 不调 LLM，纯规则补索引 |
| 正常 | 中等长度，有事实 | 常规提炼 |
| 完整 | 多概念+wikilink/category=思考，或大文件（>20KB） | 深度提炼，多角度论证 |
| 待补充 | confidence=low，缺 frontmatter 信息 | 暂不提炼，建议补充后再处理 |

**操作模式判定**：

| 场景 | 操作 |
|------|------|
| 原料自成体系，丹房无同主题 | **建新页** |
| 丹房已有同主题页 | **补充**（追加到末尾，上品页需用户确认） |
| 含矛盾/待验证点 | **递条**（标注 ⚡矛盾，当前无工具支撑） |
| 信息不足 | **存原料**，不提炼 |

---

## 规则详情（按需加载）

### 规则① 知识注入（用户提问时）

**触发**：用户提出问题或询问某个概念

**先分类，再动手**：调用 inject 前，先判断问题类型，匹配正确的工具：

| 问题类型 | 特征词 | 正确工具 | 理由 |
|---------|--------|---------|------|
| 画像/身份类 | "我是谁""我是什么样的人""我的特征" | `context_load`（已内置画像三层） | 画像数据存 `画像/` 目录，不在丹房页，inject 搜不到 |
| 知识/概念类 | "什么是X""解释Y""X与Y有什么关系" | `knowledge_inject`（或 `knowledge_recall` 宏） | 丹房知识页，主场景 |
| 记忆/教训类 | "之前发生了什么""上次怎么做的" | `context_load` / `memory_search` | 灵识记忆银行 |
| 操作/历史类 | "什么时候改的""怎么改的" | `knowledge_search`（日志自动追加） | 日志/体检记录 |

**不确定分类时**：优先调 `lingshi_classify(question)` 工具自动分流——返回 `{category, recommended_tool, confidence, reason}`，AI 直接按推荐调对应工具。

**动作**：确定是概念类后，调 `knowledge_inject`（传入关键词），返回值附带 `registry_context`（灵台内容注册表统计）
**判断**：`found=true` → 融入回复；`found=false` → 正常回复（工具已自动 fulltext_search 补盲）
**Token 约束**：同一会话内连续追问同一话题时跳过 inject（仅首轮注入）。话题切换时再调。
**推荐**：优先用 `knowledge_recall` 宏（inject + search 一步完成）

### 规则② 自动学习（用户提供信息时）

**触发**：用户提到具体事实（日期、人名、项目、数字、决策）
**动作**：调 `knowledge_save`，传入内容 + 分类（可选）+ 来源（默认"对话"）
**判断**：保存成功 → 提示用户"已存入灵台原料目录"

**不学习的内容**：
- 模糊观点（我觉得、可能、也许）
- 闲聊（今天天气不错）
- 重复信息
- **对话产出已直接写入丹房页**：不额外存原料副本

### 规则③ 关联推荐（讨论话题时）

**触发**：讨论某个主题
**动作**：调 `perception_recommend`（传入话题）
**判断**：`found=true` → 在回复末尾推荐相关页面

### 规则④ 启动协议（新会话时）

**触发**：新会话开始

**动作**：四步启动协议，按顺序强制执行：
1. **读目录地图**（AGENTS.md §一）
2. **调 `context_load`（懒加载缓存）** — 返回画像三层 + 教训 + 约束集 + 工作印记 + 场景分支 + **人格口吻**（`persona_voice`）+ **路由速查**（`route_tldr`）
3. **灵识记忆补充**（按需）— `memory_search(keyword="lesson", min_confidence=0.5)`
4. **融合背景** — 将上述信息作为对话背景

**禁止**：未调 `context_load` 就回答画像类/记忆类问题。

### 规则⑤ 知识管线（完整版）

```
knowledge_search(keyword)
  ├── 第一层：丹房检索（自动）
  │   └── 命中 → "灵台·丹房"
  ├── 第二层：原料回退（自动）
  │   ├── 高置信度(≥4.0) → "灵台·原料"
  │   └── 低置信度(<4.0) → 自动联网补充 "raw+web"
  ├── 第三层：联网回退（自动，AnySearch → Tavily）
  │   └── 命中 → "AnySearch"/"Tavily"
  └── 第四层：非丹房资产全文搜索（自动，v3 新增）
      └── fulltext_search（技能/原料/作品/外部参考）→ "灵台·外部"
```

**知识←→记忆桥接层**（自动）：每条 `knowledge_search`/`knowledge_inject` 自动查记忆银行，返回 `memory_hits` 字段。冲突时记忆覆盖（时效性优先）。

**证据契约字段使用指南**（P3——`knowledge_search` 每条结果附带）：
- `evidence`（命中理由）→ `exact_title_match`/`high_vector_match`/`keyword_exact`/`weak_semantic`/`graph_relation`。精确匹配直接引用，弱语义匹配注明"疑似相关"。
- `create_safety`（新建安全信号）→ `exists`（已有同标题，不建新页，改补充）、`probable`（建议检查后再建）、`unknown`（自由创建）。
- `memory_hits`（记忆桥接命中数）→ ≥1 条且置信度≥0.7 时以记忆为锚点（时效优先）；空或<0.7 时以知识为锚点。
- `last_updated`（页面最近更新）→ >90 天时注意时效性，优先引用更新版本。

**记忆←→知识冲突策略**（双轴裁决 | 替代"冲突时记忆覆盖"一刀切）：
`knowledge_search`/`knowledge_inject` 返回的 `memory_hits` 与知识结果冲突时，按此策略裁决：

| 丹房品级 | 记忆置信度 | 胜出方 | 理由 |
|---------|-----------|-------|------|
| 上品 | 任何 | 丹房（除非记忆≥0.95） | 上品经多角度论证，短期经验不足以覆盖 |
| 中品 | ≥0.7 | 记忆 | 中品常规内容，近期经验更贴合当前判断 |
| 中品 | <0.7 | 丹房 | 记忆置信度不足，以知识为准 |
| 下品 | ≥0.5 | 记忆 | 下品初始内容，记忆比知识更可靠 |
| 下品 | <0.5 | 丹房 | 双方都弱，以知识为准 |
| 无对应丹房页 | — | 记忆 | 知识不存在时全凭记忆 |

**检索降级标注**（P1-2）：`knowledge_search` 返回的 `degraded_reason` 字段标识了搜索的实际数据来源。非丹房层命中时，AI 回复末尾用一行括号标注来源，格式为 `（来源：{层}，{数量条}）`。示例：（来源：原料层，3 条） / （来源：联网搜索，1 条） / （来源：全文搜索，技能 1 条 + 原料 2 条）。**来源层非单一且命中多条时注明数量**，让用户清楚命中密度。不作「抱歉」或「警告」语气，纯事实陈述。`knowledge_recall` 已隐藏（宏，AI 用 inject+search 两步即可）。

**日志自动追加**（自动）：含"上次""什么时候""怎么改""日志""操作记录"等操作历史特征词的查询，自动调 `system_search_logs` 追加到返回结果。

**结果可验证例外**：丹房命中 + 置信度 ≥ 0.8 + 精确匹配 → 可跳过后续步骤，但须显式标注置信度。

**检索记录格式**（人工回溯时参考）：
```
检索记录：
1. query("关键词") → 0 结果
2. search("关键词") → 0 结果
3. fulltext_search("关键词") → 0 结果
结论：已完整检索灵台全库，当前未检索到匹配信息。
```

### 规则⑥ 观察反馈（收尾批量处理）

**触发**：调用 `knowledge_save` 后，返回值包含 `observation_feedback`
**动作**：**不在对话中途告知**。收尾时（`session_end` 宏）汇总所有 `observation_feedback`，统一告知用户
**原则**：灵识是沉静观察者——话少但记得多
**例外**：用户显式叫"灵识"或"内观"时实时响应

### 规则⑦ 记忆同步（用户偏好/习惯/特征时）

**触发**：用户透露个人偏好、工作习惯、使用特征（不是知识/事实）
**动作**：调 `user_push(key, value, category)`
**判断**：返回 `action: "created"` 或 `"updated"` → 静默完成

**与规则②的区别**：
- `knowledge_save` → 写知识（事实/案例/观点）→ 原料目录 → 需提炼
- `user_push` → 写画像（偏好/习惯/特征）→ profile.json → 即时生效

### 规则⑧ 记忆银行（mem_* 九件套）

记忆银行（MemoryBank）是与 `user_push` 完全独立的子系统。存储结构化记忆条目，含6级信源分级、冲突检测、衰减调度——一套完整的记忆生命周期管理。

**版本化**：同一 topic 下存 entries 列表（不覆盖旧版本），读取时取置信度最高的 active entry
**场景分支（branch）**：工作/生活/创作/思考/通用，默认自动检测
**遗忘归档**：连续 3 周期置信度 < 0.3 自动归档，或 < 0.1 立即归档

**九个工具**：
- `mem_write` — 写入（用于教训/偏好/可泛化经验）
- `mem_query` — 查询（支持 branch 过滤 + include_archived）
- `mem_feedback` — 反馈条目（adopt +0.05 / reject -0.3）⚠️ 别与 user_feedback 搞混
- `mem_decay` — 遗忘归档管线
- `mem_stats` — 统计
- `mem_scan_conflicts` — 冲突扫描
- `memory_merge` — 跨分支 merge
- `memory_archive` — 主动归档
- `memory_link` — 记忆→知识 wikilink 桥

### 规则⑨ 用户反馈循环（收尾批量处理）

**触发**：AI 检测到用户明确纠正了之前的回复，或明确确认了某个做法
**动作**：**不在对话中途调用**。收尾时回扫对话，批量调 `user_feedback`
**判断**：
- `correction` 非空 → 用户纠正
- `correction` 留空 → 用户确认

**与 `mem_feedback` 的区别**：
- `user_feedback` → 写**用户画像**（纠正历史/确认习惯）
- `mem_feedback` → 改**记忆条目置信度**（adopt/reject）

### 规则⑩ 多端上下文仲裁（新会话时）

**触发**：新会话启动，同时存在灵台 MCP + WorkBuddy 云记忆时
**仲裁优先级**：
- **读取**：灵台 MCP → WorkBuddy 云记忆（降级种子）→ 空
- **写入**：所有桌面端 → 灵台 MCP
**冲突处理**：两边数据矛盾时以灵台 MCP 为准

### 规则⑪ 跨端接力棒（对话进行时）

对话中产生的决策/进度/认知更新，以短脉冲写入记忆银行，让切换客户端后的 AI 能接住上下文。

**触发信号**：决策确认（"就这样""确定了"）、进度推进（阶段A→B）、认知更新（纠正/新偏好）
**心跳机制**：每隔约 5 轮对话自动 `memory_write(content="工作印记：[摘要]", tags=["context-bridge", "heartbeat"])`
**写入格式**：`memory_write(content="1-3句结论", tags=["context-bridge", "{decision|progress|cognition}"])`

### 规则⑫ 会话产出归档（用户触发）

**触发词 + 动作**：

| 触发词 | 场景 | 动作 |
|-------|------|------|
| "结束" | 会话自然收尾 | **先调 `session_end()` 宏**（写工作印记+画像+记忆），再决定是否跑归档流程 |
| "总结到灵台" | 对话中途，提炼当前段 | 执行归档流程：扫描对话产出，提取四类信号 |

**"结束"主流程**：

1. **必做**：调 `session_end()` 宏（传 `session_start` 参数，如 `"2026-07-20T15:35"`）
   - `memory_write(tags=["协作者-工作印记","session_end"])` → 跨端可见，带时间范围+决策+偏好+git commit
   - `user_feedback` → 用户纠正/确认
   - `user_push` → 画像更新
   - 情景记忆归档
2. **可选**：若本轮有知识类产出（提炼/波及/新页），继续归档流程
   - 扫描对话产出，提取四类信号（决策/结论、概念关联/修正、架构/设计洞察、议题/待办）
   - 生成候选卡片（融入既有页 / 新页 / 日志条目）
   - 用户逐条确认
   - 确认后执行

**快速通道**："结束"时，若本轮产出已全部 git commit（规则/日志/工作记忆均已落地），跳过候选卡片扫描，直接走机械收尾。避免重复提取已入库信号。

可多次"总结到灵台"，最终"结束"收尾。

### 规则⑬ 画像层候选检测（收尾批量处理）

**触发**：收尾时回扫对话，检测用户是否说出涉及自我认知/价值判断/存在方式的表述

**信号词**：

| 信号词 | 类别 |
|--------|------|
| 「我是…」「我不是…」 | 身份声明 → IDENTITY 候选 |
| 「我追求…」「我在意的是…」 | 价值排序 → SOUL 候选 |
| 「我发现…」「原来我…」 | 认知突破 → SOUL/IDENTITY 候选 |
| 「我习惯…」「我一般先…」 | 操作模式 → SKILL 候选 |

**不学习**：一次性情绪、闲聊中的身份表述、已有同类候选

### 规则⑭ 画像三层注入

**已封装进 `context_load`**，返回 `layers.画像三层`。按需触发存在层（`画像/我是谁.md`），非每轮必读。

### 规则⑮ 画像维护（证据链 + 偏离检测）

**触发**：每会话末尾（session_end）由灵识执行证据扫描；每日画像巡检（20:00）输出报告。

**阶段一：证据链扫描（灵识每会话执行，自动）**

灵识在每场对话结束时，扫描当会话用户行为 → 对每条 `[已确认]` 画像条目打 evidence_flag：
- ✅ `implicit_confirm` — 行为与画像描述一致（隐性印证，沉默续期）
- ⚠️ `subtle_drift` — 行为有偏差但不剧烈（累积 >3 次且无确认穿插则引导）
- 🔴 `strong_conflict` — 行为与画像描述明显冲突（立即单句引导）
- ∅ `no_signal` — 无相关行为（不记录）

判定依据详见 `画像/.meta/decay.md`「灵识判定对照表」。

**阶段二：行为偏离检测（画像巡检执行）**

对比最近 7 天观察 vs 心性各维度（偏差 > 40% 且非单次事件）→ 输出偏离报告。

**画像巡检逻辑**：`画像/.meta/decay.md` v2 证据链规范 + `.tool/巡更/画像巡检.md`。

### 规则⑯ 工程底线（硬校验 + 降级）

**硬校验**（`schema_validator.py`，代码层强制执行）：
- 页面路径必须匹配 `丹房/{域编号}-{域名}/{页面名}.md` 格式
- frontmatter 必须含 `标题` 字段
- 违规直接拒绝写入

**降级模式**（`degradation.py`）：
- L0：全功能
- L1：部分子系统不可用（丹房/原料/记忆银行）
- L2：仅画像+规则可用

### 规则⑰ 端无关与能力协商

灵台 MCP 是**知识层**，不与任何桌面端绑定。所有动作能力由客户端声明（capability manifest）。

**能力分三档**：
- **knowledge 层**（灵台本体产出）：`text_output` / `file_write` / `skill_system`
- **communication 层**（知识表达与输入）：`visualize` / `image_gen` / `connector_read`
- **action 层（红线）**：`connector_write` — 灵台永不产

**产出三要素**：`content`（正文）+ `modality`（偏好形态）+ `fallback`（弱端回退）

### 规则⑱ 可验证性优先

所有产出物附带「验证方式」：
- 代码改动 → 附带可运行验证命令
- 页面创建 → 附带确认方式
- 规则改动 → 附带检查方式
- 纯知识回答 → 附带来源标注

**格式**：回复末尾加一行 `✅ 验证：[一句话确认方式]`

### 执行约束（通用）

- **静默执行**：不输出调用过程
- **仅在命中时调用**：避免无谓开销
- **尊重用户**：用户说"别记"时跳过学习
- **频率控制**：长对话每隔 3-5 轮执行一次完整自检
- **Token 意识**：同话题跳过 inject；自动化报告只输出摘要

### 角色分层原则

**灵识是主体，灵台 MCP 是工具层。**

三者关系：

| 层 | 是什么 | 在哪里 |
|----|-------|--------|
| **灵识** | WorkBuddy 对话侧的意识层 — 由 AGENTS.md 约束集 + SOUL.md 人格 + context_load 注入的画像/教训/记忆共同构成 | WorkBuddy 对话上下文 |
| **PerceptionMixin** | 灵识在灵台 MCP 侧的工具接口 — 提供 lingshi_classify/memory_* 等工具（`lingshi_inject` 已废弃，由 `context_load` 替代） | `.tool/lingtai-kb/server_mixins/perception.py` |
| **灵台 MCP** | 工具层 — 知识系统、记忆银行、画像层、观察层 的底层实现 | `.tool/lingtai-kb/` |

灵识有两种工作模式：

| 模式 | 职责 | 切换时机 |
|----|------|---------|
| **对话模式**（默认） | 执行任务、直接对话 | — |
| **内观模式** | 回扫对话、观察反馈、纠正检测、画像候选、知识管理 | 收尾时自动，或用户叫"灵识"/"内观"时 |

灵识实时介入的唯一场景：用户显式叫"灵识"或"内观"——其他时候保持对话模式。

---

## 收尾流水线完整版

每次对 `edrc/` 仓库完成实质性操作后执行。

### 5.1 写丹房日志（丹房页操作 → 自动）

往 `丹房/.meta/oplog.jsonl` 追加机读记录（人类版日志.md 已退役，工作印记替代）。

### 5.2 重建灵识索引

调 `sys_refresh_index`（quick 模式）。让灵识感知到文件变化。

### 5.3 提 Git（丹房页操作 → 自动）

非丹房页操作（AGENTS.md、巡更、配置、脚本等）需手动：
```bash
cd /{vault-path}/edrc
git add -A && git commit -m "类型: 摘要"
```

### 5.4 波及检查（Karpathy Ingest 原则）

若本次操作涉及**"提炼"**（新建丹房页），追加此步骤：

1. 调 `ingest_ripple(new_page="丹房/xx/新页")` 识别波及页
2. 对返回的 `impacted_pages` 逐页执行：
   - 阅读目标页，确认插入位置合理
   - 调 `page_append_section` 在对应章节追加交叉引用
   - 目标：每个波及页补充一段 1-3 句的 `[!tip]`/`[!note]` 外部参照
3. 重建索引 → `sys_refresh_index`
4. 提 Git

**量化目标**：一篇新来源触及 **8-15 页**（含新页 + 5-8 波及页更新 + 索引 + 日志 + 原料标记）。

**不适用**：纯编辑/更新已有页、非提炼操作（如数据库维护、脚本修改）跳过此步。

### 5.5 文档对齐

改了代码/流程后，检查三层知识是否同步：AGENTS.md / 丹房页 / README.md。

### 5.6 MCP 刷新（改代码后必做）

改了 `.tool/lingtai-kb/` 代码后调 `sys_reload` MCP 工具热重载。
不可用时下次重启生效。

### 5.6.1 MCP 测试纪律（改动 memory_bank/ 后必须隔离）

**铁律**：凡测试会落盘的代码（记忆银行、audit、leak_ledger），必须在构造期注入隔离目录：

```python
import tempfile
tmp = tempfile.mkdtemp()
sandbox_data = Path(tmp) / "data"; sandbox_data.mkdir()
mb = MemoryBank(vault_path=tmp, data_dir=str(sandbox_data))
```

不传 `data_dir` 的 `MemoryBank()` 一律走真实路径。

### 5.7 教训归档

回顾本会话是否发现了可复用的操作教训：
- **有** → `mem_write(content="错误 + 正确做法", tags=["lesson"])`
- 已存在同类教训 → `mem_feedback(memory_id, action="adopt")` 增强置信度

### 5.8 灵识批量处理

收尾时回扫对话，执行：
1. **观察反馈汇总**（规则⑥）
2. **纠正/确认检测**（规则⑨）
3. **画像候选检测**（规则⑬）
4. **偏好/习惯同步**（规则⑦）
5. **工作印记写入**（规则⑪）

**推荐**：直接调 `session_end()` 宏工具一步完成。

---

## 教训生命周期（完整）

教训是 AI 从操作中发现的模式——某个做法不对、某类工具该选哪种用法。经过三次确认后"毕业"为正式规则写入 AGENTS.md。

### 教训来源

| 来源 | 示例 | mem_write 信源 |
|:----|:-----|:--------------|
| AI 操作失误 | 检索违规、用错证据类型 | `ai_reasoning` |
| 用户纠正 | "别啰嗦"、"不是那个意思" | `user_correction` |
| 重复模式 | 连续同类文件结构问题 | `hebbian` |

### 写入时机（收尾时）

有教训 → `mem_write(content="错误描述 + 正确做法", tags=["lesson"])`
已存在 → `mem_feedback(memory_id, action="adopt")` 增强

### 加载时机（启动时）

`context_load` 自动注入高置信度活动记忆（`layers.灵识记忆.lessons`）。

### 毕业条件

同一 pattern 的教训满足任一条件即毕业：
1. **attempt ≥ 3** — 被记录或增强 3 次以上
2. **用户明确确认** — 用户说"对，记下来"
3. **SkillOpt 验证通过**

毕业动作：写入 AGENTS.md → `mem_feedback` 标记毕业 → 日志记录

---

## 自动化维护规则

改自动化只改 `巡更.md` 文档，不动 Automation prompt。prompt 只引用文档路径，所有逻辑在 .md 里。

---

## SkillOpt 验证门控（改规则前必读）

修改 `巡更/*.md` 或 `AGENTS.md` 前：
1. **读测试用例** — 打开 `体检/灵识-skillopt状态.md#🧪-验证测试套件`
2. **跑基准** — 用当前规则过一遍用例
3. **改规则** — 只改一处
4. **验证** — 同一组用例重新跑，通过率不降才接受
5. **归档** — 被拒绝的修改方向记入测试用例末尾

---

## 宏工具（服务端组合封装）

| 宏工具 | 封装组合 | 对应规则 |
|--------|---------|---------|
| `knowledge_recall` | inject + search 一步完成 | 规则①⑤ |
| `session_end` | user_feedback + user_push + knowledge_save + memory_write | §5.7 |

**使用原则**：
- `knowledge_recall` 优先于手动分步调 inject + search
- `session_end` 替换 §5.7 的手动单步调用

---

## 灵识证据链规程（v2，替代会话数衰变）

> 灵识在每场对话结束时，自动执行 `画像证据扫描（image_evidence_scan）`。
> 职责：扫当会话用户行为 → 对每条 `[已确认]` 画像条目判据 evidence_flag → 追加到 `画像/.meta/decay.md` 证据链。

### 触发时机

每会话末尾，`session_end()` 调用前（或作为 session_end 的一部分）。不依赖定时巡检。

### 扫描输入

灵识从当会话中提取以下信号源：

| 信号源 | 来源 | 示例 |
|--------|------|------|
| 用户查询关键词 | 当会话所有 query | "含人量""O与π" → 追问驱动 |
| 用户纠正 | 如果用户说了"别这样""太长了"等 | → 负向触发-啰嗦回复 |
| 丹房操作类型 | 提炼/补角/纯维修 | → 正向触发-跨域连接 / 决策模式 |
| 回答偏好 | 用户是否要求简洁/例证/反驳 | → 对协作者要求 |
| 对话语气 | 追问 vs 浅问 | → 投入信号 |

### 判定流程

```
for 每条 [已确认] 条目 in 画像三层:
    对照 decay.md「灵识判定对照表」匹配当会话行为

    if 匹配到「强烈矛盾信号」:
        flag = strong_conflict
        → 追加到证据链
        → 也可即时弹一句确认（不强制，取决于 context 宽松度）
    
    elif 匹配到「轻微偏移信号」:
        flag = subtle_drift
        → 追加到证据链
        → 不弹窗
    
    elif 匹配到「隐性印证信号」:
        flag = implicit_confirm
        → 追加到证据链
        → 沉默（画像续期）
    
    else:
        → 不记录任何内容（∅ no_signal）
```

### 写入格式

每条 flag 追加到 `画像/.meta/decay.md` 对应条目的证据链末尾：

```
  [YYYY-MM-DD] <flag_type> → <简短理由>
```

### 自保规则

- **同一信号不重复**：如果当会话有 10 次含人量搜索，只记 1 条 `implicit_confirm`（灵识判定对照表"追问驱动"匹配到一次就算）
- **不猜测动机**：用户行为有多个解释可能时，取最低信号等级（如可解释为 subtle_drift 也可为 implicit_confirm → 取 subtle_drift）
- **不代替用户确认**：implicit_confirm 只是证据累积，不等同于"用户确认了这条画像"。显式确认才更新 `📌 最后显式确认` 时间戳

---

<!-- schema-rules
hard:
  - type: path_pattern
    match: "丹房/\\d{2}-[^/]+/.+\\.md"
    message: "丹房页面路径格式：丹房/{域编号}-{域名}/{页面名}.md"
  - type: frontmatter_field
    field: "标题"
    required: true
    message: "丹房页面必须有 frontmatter '标题' 字段"
soft:
  - type: paragraph_length
    max: 500
    message: "段落建议不超过 500 字符"
  - type: broken_wikilink
    warn: true
    message: "检测到可能断裂的 [[wikilink]]：链接目标须为文件名 slug 或 vault 相对路径，禁用 aliases（详见页面模板.md 链接目标规范）"
-->