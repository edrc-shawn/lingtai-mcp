# 灵台 (Lingtai)

> **A self-evolving knowledge management system.** It gathers materials, refines knowledge, evolves its own rules, runs health checks, and even writes and publishes content for you — all while you sleep.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP Protocol](https://img.shields.io/badge/MCP-2024--11--05-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)

---

## What is Lingtai?

灵台 is a three-layer knowledge system that combines an Obsidian vault, a custom MCP Server, and a suite of automated patrol tasks:

| Layer | Role | Scale |
|-------|------|-------|
| **Knowledge Base** (丹房) | Structured, refined knowledge across 13 domains | 269 pages |
| **Memory Bank** | Cross-session AI memory, user profiles, lessons | 164 memories |
| **Raw Materials** (原料) | Unprocessed inputs awaiting refinement | 1,215 entries |

At its core is **灵识** — an AI identity that serves as the cognition layer, calling 68 MCP tools to search, synthesize, refine, and evolve the knowledge base. Any MCP-compatible AI client can connect to 灵台 and instantly understand you.

---

## Architecture

```
Raw Materials → Refine → Knowledge Base (丹房) → 灵识 (Query/Reason/Associate)
                                                       ↓
                                               Health Checks (体检) → Introspection
                                                       ↓
                                               Topic Pool → Multi-platform Publishing
                                                       ↓
                                               Archive
```

### The MCP Server (`.tool/lingtai-kb/`)

A pure-Python MCP Server with **68 tools** organized into 14 domains:

| Domain | Tools | Capability |
|--------|-------|------------|
| Knowledge Search | 12 | Semantic search, graph diffusion, heatmaps, compound interest, gap detection |
| Memory System | 12 | User profiles, long-term memory, observation engine, memory graduation |
| Content Output | 4 | Multi-platform publishing (WeChat, RED, TikTok, Bilibili) |
| Material Refinement | 4 | Zero-LLM raw material pre-screening, fast refinement, status tracking |
| Macros | 6 | Knowledge synthesis, refine-in-one-step, session wrap-up, ripple analysis |
| System Tools | 8 | Index rebuild, health inspection, web search, token stats, cross-end sync |
| + 8 more domains | 22 | SkillOpt self-evolution, Agent recommendation, episodic memory, concept collision |

**Zero third-party dependencies** — the server runs on Python stdlib alone.

### The Patrol System (巡更)

8 automated tasks running 24/7 via WorkBuddy:

| Time | Task |
|------|------|
| 03:00 | **Sleep Evolution** — SkillOpt engine + memory decay + change brief |
| 08:00 | **Morning Brief** — Weather, AI news, topic suggestions → WeChat |
| 18:00 | **Daily Check** — Lint, backlinks, evolution, reconciliation, self-heal |
| 20:00 | **Profile Patrol** — Decay detection, behavior drift report |
| 21:00 | **Daily Introspection** — 400-800 word first-person memory log |
| Mon 09:00 | **Weekly Scan** — Deep archive, encoding fixes, missed materials, semantic gaps |
| 1st of Month | **Monthly Review** — Timeliness scan, rule evolution suggestions |

---

## Quick Start

### Prerequisites

- Python 3.10+
- An AI client that supports MCP (Claude Desktop, Cursor, VS Code, WorkBuddy)
- Clone this repository

### Connect Your AI Client

Add this to your MCP configuration:

```json
{
  "mcpServers": {
    "lingtai-kb": {
      "type": "stdio",
      "command": "python",
      "args": [".tool/lingtai-kb/mcp_server.py"],
      "cwd": "/path/to/灵台/.tool/lingtai-kb",
      "env": {
        "LINGTAI_VAULT": "/path/to/灵台"
      }
    }
  }
}
```

Then start a conversation and say: **"调 context_load"** — if it returns knowledge base stats, you're connected.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LINGTAI_VAULT` | Recommended | Path to the Obsidian vault root |
| `TAVILY_API_KEY` | Optional | Web search API key |
| `ANYSEARCH_API_KEY` | Optional | Alternative web search API key |
| `LINGTAI_CLIENT_ID` | Optional | Client identifier for multi-end scenarios |

---

## Project Structure

```
灵台/
├── AGENTS.md                    # AI rulebook (the "operating manual")
├── AGENTS-appendix.md           # Rules appendix + tool reference
├── .tool/
│   ├── lingtai-kb/              # MCP Server (116 Python files)
│   │   ├── mcp_server.py        # Entry point
│   │   ├── router.py            # JSON-RPC routing
│   │   ├── server.py            # Core server (12 mixin inheritance)
│   │   ├── server_mixins/       # 22 mixin modules by domain
│   │   ├── memory_bank/         # Memory persistence engine
│   │   └── skillopt/            # Self-evolution engine
│   └── scripts/                 # 46 utility scripts
├── 丹房/                         # Knowledge base (13 domains, 269 pages)
├── 原料/                         # Raw materials (1,215 entries)
├── 入门/                         # Onboarding docs
├── 技能/                         # Skill templates (13 skills)
├── 体检/                         # Health check reports
├── 画像/                         # User profile (3 layers)
└── 作品/                         # Published outputs
```

---

## Key Design Principles

1. **Knowledge ≠ Memory.** Structured knowledge lives in 丹房; personal experience lives in the Memory Bank. They're layered, not mixed.

2. **Infrastructure over model upgrades.** Good memory architecture, context engineering, and observability matter more than switching to a newer LLM.

3. **Self-evolving.** The SkillOpt engine runs nightly at 03:00, analyzing usage patterns and proposing rule refinements. Rules graduate from observations → lessons → hard rules.

4. **End-agnostic.** 灵台 produces knowledge, not presentations. Any AI client can consume it — the presentation layer is the client's responsibility.

5. **Verifiable outputs.** Every output must include a verifiable claim. "Looks fine" is a banned phrase.

---

## Contributing

See [CONTRIBUTING.md](.tool/lingtai-kb/CONTRIBUTING.md) for development guidelines.

---

## Security

- All API keys are injected via environment variables — **never hardcoded**
- File operations are sandboxed within the vault directory
- stdout is reserved for JSON-RPC protocol; all logging goes to stderr
- Write operations use a global mutex with 5s timeout
- See [SECURITY.md](.tool/lingtai-kb/SECURITY.md) for the full policy

---

## License

[MIT](.tool/lingtai-kb/LICENSE) © 2026 耳东日成
