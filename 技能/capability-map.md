---
标题: 端能力协商映射
类型: 架构参考
---

# 端能力协商映射（capability-map）

> 灵台 MCP 是知识层，与任何桌面端都不绑定。本文档定义 capability 契约与 skill 体系的端无关映射。

## 一、原则

- 灵台只生产"知识"，不生产"动作/呈现"。
- 客户端通过 `context_load(client_capabilities=...)` 声明能力，灵台返回 `capabilities` 字段。
- 规则/感知规则引用能力一律写 `capability:<name>`，不写端名。
- **能力分三档**：`knowledge`（知识层，灵台本体直接产/读）/ `communication`（沟通连接层，表达与输入通道）/ `action`（动作层，真实副作用写操作——红线）。灵台对三档姿态不同，见 §二。

## 二、capability 清单（三档）

### 档一 knowledge（知识层，灵台本体直接产/读）

| capability | 含义 | 默认 | 弱端 fallback |
|------------|------|------|---------------|
| `text_output` | 文本输出 | ✅ | — |
| `file_write` | 直接读写本地文件（知识落地） | ✅ | — |
| `skill_system` | 真 skill 运行时（体系 A 加速层） | ❌ | 走文档式 skill（体系 B）作为知识 |

### 档二 communication（沟通/连接层，表达与输入通道）

| capability | 含义 | 灵台姿态 | 弱端 fallback |
|------------|------|---------|---------------|
| `visualize` | 渲染 SVG/HTML 内联 widget（知识的**表达**通道） | 产 SVG，不绑端 | Mermaid 文本 / 描述性文字 |
| `image_gen` | 文生图（知识的**表达**通道） | 产图，不绑端 | 文字描述 / 建议外部工具 |
| `connector_read` | 接入 SaaS 读取（邮件/GitHub/Notion 等，作为知识**输入**通道） | 接收其输入为知识源；灵台不持凭证、不发起连接 | 不支持则跳过，不影响知识产出 |

> `connector_read` 类能力（如 OpenConnector / Composio 的"读接口"面）本质是**沟通/连接能力**：读邮件=理解沟通脉络，连 GitHub=理解项目脉络，连 Notion=吸收笔记——这些对知识库是增值输入，不是动作。灵台接收其产出为知识源，但**不吸收其实现、不持有其凭证**。

### 档三 action（动作层，真实副作用写操作 —— 红线）

| capability | 含义 | 灵台姿态 | 弱端 fallback |
|------------|------|---------|---------------|
| `connector_write` | 真实写操作（发邮件/发 PR/改数据） | **红线：灵台永不产** | 输出草稿 / 可执行指令清单，由端侧或人执行 |
| `automation` | 注册定时/周期任务（触发灵台自身知识例程） | 灰区：仅编排灵台**内部**知识流程，无外部副作用；一旦触发外部写则降级为 `action` | 输出可手动执行的指令清单 |

> `automation` 在灵台语境下只编排自身知识例程（如每日睡眠进化），属 knowledge 的使能器；若某桌面端把它用于触发外部系统写操作，即落入 `action` 档，按红线处理。

### 附：skill 领域能力（灵台技能域，知识资产轴）

> 与上面"三档执行能力"是**不同轴**：三档描述"客户端能做什么类型的动作"，本表描述"灵台**提供哪些领域的技能知识**"。
> 领域能力本身不执行——它是知识资产；要落地需客户端具备对应执行能力档（下表第三列），并按 §四 派生体系 A 真 skill。

| 领域 capability | 含义 | 执行所需能力档 | 体系 B 入口（详见 §四） |
|----------------|------|---------------|------------------------|
| `writing` | 公众号/小红书/抖音等图文写作（6 个 skill + 1 入口） | `text_output` | `技能/耳东日成写作/` |
| `illustration` | 耳东怪诞正文配图（含角色规范/对比分析） | `image_gen` | `技能/配图/SKILL.md` 等 |
| `video` | 耳东视频生成 | `image_gen` | `技能/视频/耳东视频生成.md` |
| `divination` | 六爻断卦——排盘+AI解读 | `text_output` | `技能/六爻断卦/SKILL.md` |
| `engineering` | skill 自进化护栏 / MCP 设计 | `text_output` + `file_write` | `技能/工程/*.md` |
| `session` | 会话洞察自动沉淀 | `text_output` | `技能/会话沉淀/*.md` |

> 协商用法：客户端 `context_load` 返回的 `capabilities` 中，除三档执行能力外，应列出上述**领域能力**——让"给文章配图"这类请求能被路由到 `illustration` 域，进而定位 §四 的 `erdong-illo` 真 skill 派生目标。

## 三、客户端接入方式

1. **显式握手（推荐）**：调用 `context_load` 时传 `client_capabilities` JSON。
   示例：`context_load(client_capabilities='{"visualize":true,"skill_system":true}')`
2. **服务端 env 兜底**：设环境变量 `LINGTAI_CAPABILITIES`（完整 manifest JSON）或 `LINGTAI_CLIENT`（WorkBuddy/Reasonix/QoderWork/MimoCode，命中预置清单）。
3. **默认最小集**：未声明时仅有 `text_output` + `file_write`。

能力来源优先级：显式声明 > `LINGTAI_CAPABILITIES` > 预置已知端清单 > 默认最小集。

## 四、skill 体系映射（体系 B ↔ 体系 A）

- **体系 B（文档式 skill）**：`技能/` 下的 markdown 文档，与端无关，是源资产。任何端都能把它当知识 `inject` / `search`。
- **体系 A（真 skill）**：客户端原生 skill 运行时（如 WorkBuddy 的 `.workbuddy/skills/`）。仅在声明 `skill_system` 的端上，把体系 B 文档"编译"为体系 A 真 skill 注入，作为加速层。

映射表（覆盖 `技能/` 下全部自有 skill，不含 `外部参考/`；`模板/` 为脚手架、`配图/references/` 为配图子文档，均不单列）：

| 文档式 skill（体系 B） | 真 skill（体系 A） | 适用端 |
|------------------------|--------------------|--------|
| `技能/耳东日成写作/专治AI味儿.md` | 待生成 `anti-ai-tone` | 支持 skill_system 的端 |
| `技能/耳东日成写作/长文出稿工作流.md` | 待生成 `gzh-publish` | 支持 skill_system 的端 |
| `技能/耳东日成写作/长篇/SKILL.md` | 待生成 `gzh-longform`（含风格参考） | 支持 skill_system 的端 |
| `技能/耳东日成写作/中篇/SKILL.md` | 待生成 | 支持 skill_system 的端 |
| `技能/耳东日成写作/短篇/SKILL.md` | 待生成 | 支持 skill_system 的端 |
| `技能/耳东日成写作/耳东日成小红书.md` | 待生成 `xhs` | 支持 skill_system 的端 |
| `技能/耳东日成写作/耳东日成抖音.md` | 待生成 `douyin` | 支持 skill_system 的端 |
| `技能/工程/skill.evolve-guard.md` | 待生成 `evolve-guard` | 支持 skill_system 的端 |
| `技能/工程/skill.mcp-design.md` | 待生成 `mcp-design` | 支持 skill_system 的端 |
| `技能/视频/耳东视频生成.md` | 待生成 `video-gen` | 支持 skill_system 的端 |
| `技能/配图/SKILL.md` | 待生成 `erdong-illo` | 支持 skill_system 的端 |
| `技能/配图/erdong/erdong-ip.md` | 待生成 `erdong-ip` | 支持 skill_system 的端 |
| `技能/配图/配图对比分析.md` | 待生成 `illo-compare` | 支持 skill_system 的端 |
| `技能/会话沉淀/会话洞察自动沉淀.md` | 待生成 | 支持 skill_system 的端 |

> 派生规则：真 skill 永不取代文档式 skill。文档更新后，真 skill 应重新派生。

## 五、协作约定

- 写规则时：`当 capability:visualize 可用 → 输出 SVG widget；否则 → Mermaid 文本`。
- 不要：`用 WorkBuddy 的 Visualizer 画图`（绑定端名）。
- 灵台产出封装：`content + modality 偏好 + fallback`。
