## 十二、工程参考：Memoir 真实实现

> 审查时间：2026-07-06
> 代码位置：`存档/官方参考/记忆架构/memoir-package/memoir-source/`
> 项目性质：Apache-2.0 许可的生产级 Git 记忆系统

Memoir 是一个**真实运行的 Git 式记忆系统**，不是概念设计。它的架构验证了我们方案中的几乎全部决策，并在几个关键点上提供了更精细的参考。

### 12.1 存储架构

```
┌──────────────────────────────────────────────┐
│  ProllyTree / VersionedKvStore （Merkle Tree）  │
│  每次写入 → 新 root hash → 自动 Git commit     │
├──────────────────────────────────────────────┤
│  后端（可切换，创建后固定）：                    │
│  ┌────────┬────────┬─────────┬──────────┐    │
│  │  Git   │  File  │  RocksDB │  Memory  │    │
│  │(默认)  │(新默认) │ (生产)   │ (测试)   │    │
│  └────────┴────────┴─────────┴──────────┘    │
├──────────────────────────────────────────────┤
│  对象存储：3种粒度                             │
│  - blob（原始内容）                           │
│  - tree（键的组织结构）                       │
│  - commit（原子写入 + metadata + parent）     │
└──────────────────────────────────────────────┘
```

### 12.2 与我们方案的对应关系

| 我们的方案 | Memoir 实现 | 差距 |
|-----------|------------|------|
| append-only 版本链 | ✅ ProllyTree 每次写入产生新 root hash | 架构一致 |
| branch 场景隔离 | ✅ 完整 `list_branches()` / `checkout()` / `merge()` | 实现更完整 |
| 合并策略 | ✅ **6 种策略**（WE 仅 3 种） | **我们的方案需补** |
| diff-aware 写入 | ✅ 内容哈希去重 + 同 key 版本化 | 一致 |
| 置信度 + 衰减 | ✅ CONFIDENCE_GATED 策略 + 置信度比较 | 一致 |
| 隐私安全 | ✅ `gc.auto=0` 避免意外丢失，`gc.pruneExpire=never` | **需增加** |
| 冷热分层 | 未显式实现（所有 commit 都在 git 中） | Memoir 弱于此 |

### 12.3 Memoir 的精妙设计：Merge Policy

这是我们的方案最应该借鉴的部分。Memoir 定义了 **4 种记忆类型 × 对应默认合并策略**：

| 记忆类型 | 例子 | 默认策略 | 含义 |
|---------|------|---------|------|
| **WORKING** | 当前 task 上下文、暂存 | REPLACE | 最后写入覆盖，不保留历史 |
| **EPISODIC** | 对话历史、事件流 | APPEND | 追加到条目列表，受 cap 限制 |
| **SEMANTIC** | 事实、偏好、知识 | **CONFIDENCE_GATED** | 新置信度 >= 现有才写入 |
| **PROCEDURAL** | 技能、流程、workflow | LLM_MERGE | 新老内容让 LLM 合并 |

**核心洞察：不是所有记忆都需要版本化。** WORKING 和 EPISODIC 用简单策略，SEMANTIC 做置信度门控，PROCEDURAL 用 LLM 合并——每种记忆类型有其自然的合并方式。

CONFIDENCE_GATED 策略尤其适合灵台：新信息置信度 >= 现有才写入，低于则跳过（不产生 commit）。这就是我们方案的 **diff-aware write** 的工程化实现。

### 12.4 可以借鉴的工程细节

| 借鉴点 | 具体做法 | 灵台适配 |
|-------|---------|---------|
| **schema_version** | 记忆对象带版本号，v1→v2 懒升级 | 当前 Memory dataclass 可加 `schema_version=1` |
| **entries（facet）** | 每个 key 存时间戳条目列表，受 MAX_ENTRIES 限制 | **可替代我们的版本链**——每条 topic 下存 entries 列表而非独立 memory_id |
| **冲突策略枚举** | APPEND / REPLACE / CONFIDENCE_GATED / LLM_MERGE / MERGE_ON_READ / REJECT | 当前只有 fork/deprecate/pending，需扩展 |
| **Git gc 防护** | `gc.auto=0`, `gc.pruneExpire=never` | 灵台已有 git，加这两条配置 |
| **backend lock** | `<store>/.git/memoir-backend` 记录后端类型 | 灵台不需要（本身就是 JSON 文件）|
| **记忆类型识别** | 按 key 前缀自动推断类型 | 可加 `memory_type` 字段替代静态分类 |

### 12.5 Memoir 不做而我们需要的

| 能力 | Memoir | 灵台需要 |
|------|--------|---------|
| 灵识多层级（画像/观察/系统） | ❌ 单一记忆池 | ✅ 已有四层 |
| 冷热分层 / 遗忘归档 | ❌ git 全量保留 | ✅ 需要 |
| 自动场景检测 | ❌ 用户手动分支 | ✅ 可自动 |
| 观察层模式归纳 | ❌ | ✅ 已有 |
| MCP 工具集成 | ❌ 独立 | ✅ 已有 |

### 12.6 对方案的影响

**不改框架，但 §六~§七 需加几条细节：**

1. **记忆类型化** — Memory dataclass 增加 `memory_type` 字段（working/episodic/semantic/procedural），决定默认合并策略
2. **合并策略扩展** — 从 fork/deprecate/pending 扩展为 APPEND / REPLACE / CONFIDENCE_GATED / LLM_MERGE / MERGE_ON_READ / REJECT 六种
3. **entries（facet）取代独立版本链** — 每个 topic 下存 entries 列表（时间戳 + 内容 + 置信度 + 来源），受 MAX_ENTRIES 限制，简化存储模型
4. **Git gc 防护** — 在 git 仓库加 `gc.auto=0` + `gc.pruneExpire=never`

```python
# 改造后的 Memory dataclass（参考 Memoir）
@dataclass
class Memory:
    id: str                    # "mem_" + sha256[:12]
    content: str               # 最新内容（投影）
    entries: list              # [{"content", "confidence", "timestamp", "source", "status"}, ...]
    schema_version: int = 2    # v2 = entries-based; v1 = legacy flat content
    memory_type: str = "semantic"  # working / episodic / semantic / procedural
    merge_strategy: str = "confidence_gated"  # 由 memory_type 决定默认值
    source: str = "user_stated"
    current_confidence: float = 0.4
    status: str = "active"
    branch_id: str = "main"
    tags: list = field(default_factory=list)
    created_at: str = ""
    last_verified: str = ""
    conflicts_with: list = field(default_factory=list)
```

### 12.7 验证

在方案文档所在的灵台仓库中，验证 git gc 防护是否已配置：

```bash
git config gc.auto
git config gc.pruneExpire
```