# Changelog

## v4.0.0 (2026-07-15)

### 开源工程规范

- **LICENSE**: 添加 MIT 许可证 ([#1](LICENSE))
- **SECURITY.md**: 添加安全策略和漏洞报告流程
- **CONTRIBUTING.md**: 添加贡献指南和开发规范
- **.env.example**: 添加环境变量模板
- **README.md**: 重写为开源版本（功能概览、架构图、工具清单、安全声明）
- **CHANGELOG.md**: 添加变更日志

### 安全修复

- **移除硬编码 API Key**: `server_mixins/shared.py` 中 AnySearch 默认密钥改为空字符串，强制通过环境变量注入
- **加固 .gitignore**: 扩展覆盖 `.cache/`, `logs/`, `data/`, `.env`, `*.bak`, `.pytest_cache/` 等运行时目录
- **修复 stdout 污染**: `router.py` 中 JSON-RPC 错误处理路径改用 `sys.stdout.write()` 替代 `print()`，避免协议流污染

### 协议合规

- 所有工具已标注 `readOnlyHint` / `destructiveHint`（MCP 2024-11-05 协议）
- 自定义 JSON-RPC 实现，无第三方 SDK 依赖

### 变更记录追踪

- `.gitignore` 扩展覆盖运行时目录（`*.bak`, `.env`, `.cache/`, `logs/`, `data/` 等）
- `server_mixins/shared.py`: `ANYSEARCH_API_KEY` 默认值从硬编码密钥改为空字符串
- `router.py`: `except json.JSONDecodeError` 处理路径改用 `sys.stdout.write()`