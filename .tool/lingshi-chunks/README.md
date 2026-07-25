# lingshi-chunks 部署说明

> 结构化索引模块 — 将丹房页 Markdown 提取为语义原子（chunk），文件级 JSON 存储。

---

## 部署到零台

将 `lingshi-chunks/` 目录复制到零台 vault 的 `.tool/` 下：

```bash
cp -r lingtai-os/.tool/lingshi-chunks/ /path/to/lingtai/.tool/lingshi-chunks/
```

### 验证安装

```bash
cd /path/to/lingtai
python .tool/lingshi-chunks/cli.py stats
# → 输出：结构化索引统计（chunk 总数: 0）

python .tool/lingshi-chunks/cli.py reindex
# → 全量重建丹房页索引

python .tool/lingshi-chunks/cli.py search "O与π"
# → 搜索结构化 chunk
```

---

## CLI 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `extract <path>` | 从单篇丹房页提取 chunk | `cli.py extract 丹房/00-思考与认知/O与π.md` |
| `reindex` | 全量重建所有丹房页索引 | `cli.py reindex` |
| `search <query>` | 搜索 chunk | `cli.py search "O与π"` |
| `stats` | 索引统计 | `cli.py stats` |
| `status` | 索引状态 | `cli.py status` |
| `show <chunk_id>` | 查看单条 chunk 详情 | `cli.py show chunk_7a9f3c21` |

所有命令支持 `--vault=<path>` 指定 vault 路径，默认读取 `$LINGTAI_VAULT`。

---

## 与现有 lingshi 的关系

```
lingshi（现有）：index.json → 文件级检索 → 返回整篇丹房页
lingshi-chunks（新增）：chunks/ → 语义原子级检索 → 返回高密度片段

互补关系，不冲突。用户可同时使用两者。
```

---

## 架构

```
lingshi-chunks/
├── core.py              ← 共享逻辑层
│   ├── StructuredChunk  → 数据模型 + 校验 + id 生成
│   ├── ChunkStore       → 文件级 JSON 存储 + index.json + manifest
│   ├── NaiveSearch      → 朴素文本搜索（占位，后续替换为向量）
│   ├── Extractor        → 从 Markdown 提取 chunk（规则版，后续接 LLM）
│   └── StructuredIndex  → 统一入口
├── cli.py               ← CLI 入口
├── mcp_adapter.py       ← MCP 适配器（骨架）
└── test_verify.py       ← 验证测试
```

---

## 后续路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| 1 | CLI 验证管线（当前） | ✅ 完成 |
| 2 | 接入 LLM 提取（使用 extraction-prompt.md） | ⏳ 待做 |
| 3 | 向量搜索替换 NaiveSearch | ⏳ 待做 |
| 4 | 封装为 MCP 工具（mcp_adapter.py → 注册到主 server） | ⏳ 待做 |
| 5 | 覆盖度警告 + Token 预算控制 | ⏳ 待做 |