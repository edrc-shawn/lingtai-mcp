# -*- coding: utf-8 -*-
"""
灵台MCP - 自检系统
==================
检查灵识MCP注入链路是否通畅，支持自动修复。

检查项：
1. MCP配置 → mcp.json 中 lingtai-kb 配置是否存在
2. MCP服务器 → mcp_server.py 文件是否存在且可执行
3. 数据源 → 丹房/.meta/index.json 是否可读
4. LLM配置 → models.json 中是否有可用API
5. 依赖库 → requests 是否安装
6. Token数据库 → token_monitor.db 是否正常

修复能力：
- MCP配置缺失 → 自动创建配置
- MCP配置禁用 → 自动启用
- 数据源缺失 → 自动重建索引
- 依赖库缺失 → 自动安装
- Token数据库损坏 → 自动重建

用法：
    python 灵台/.tool/lingtai-kb/selfcheck.py           # 仅检查
    python 灵台/.tool/lingtai-kb/selfcheck.py --fix     # 检查并修复
"""

import os, sys, json, subprocess
from pathlib import Path
from datetime import datetime
from typing import Tuple, Optional, List


# 路径常量
VAULT = Path(r".")
WORKBUDDY = Path(os.path.expanduser("~")) / ".workbuddy"
MCP_CONFIG = WORKBUDDY / "mcp.json"
MCP_SERVER = VAULT / ".tool" / "lingtai-kb" / "mcp_server.py"
INDEX_JSON = VAULT / "丹房" / ".meta" / "index.json"
MODELS_JSON = WORKBUDDY / "models.json"
TOKEN_DB = VAULT / ".meta" / "token_monitor.db"
BUILD_INDEX_SCRIPT = VAULT / ".tool" / "scripts" / "build_index.py"

# 默认MCP配置
DEFAULT_MCP_CONFIG = {
    "mcpServers": {
        "lingtai-kb": {
            "command": str(WORKBUDDY / "binaries" / "python" / "versions" / "3.13.12" / "python.exe"),
            "args": [str(MCP_SERVER)],
            "enabled": True,
            "disabled": False
        }
    }
}


class CheckResult:
    """单项检查结果"""
    def __init__(self, name: str, status: str, detail: str = "", 
                 suggestion: str = "", fixable: bool = False, fix_func=None):
        self.name = name
        self.status = status  # ✅ / ⚠️ / ❌
        self.detail = detail
        self.suggestion = suggestion
        self.fixable = fixable
        self.fix_func = fix_func
        self.fixed = False
        self.fix_detail = ""
    
    def __repr__(self):
        s = f"{self.status} {self.name}: {self.detail}"
        if self.suggestion:
            s += f"\n   → {self.suggestion}"
        if self.fixed:
            s += f"\n   → 🔧 已修复: {self.fix_detail}"
        return s
    
    def fix(self) -> bool:
        """执行修复"""
        if not self.fixable or not self.fix_func:
            return False
        
        try:
            success, detail = self.fix_func()
            self.fixed = success
            self.fix_detail = detail
            return success
        except Exception as e:
            self.fix_detail = f"修复失败: {e}"
            return False


# ========== 修复函数 ==========

def _fix_mcp_config_missing() -> Tuple[bool, str]:
    """修复：创建MCP配置"""
    try:
        WORKBUDDY.mkdir(parents=True, exist_ok=True)
        with open(MCP_CONFIG, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_MCP_CONFIG, f, indent=2, ensure_ascii=False)
        return True, f"已创建 {MCP_CONFIG}"
    except Exception as e:
        return False, str(e)


def _fix_mcp_config_disabled() -> Tuple[bool, str]:
    """修复：启用MCP配置"""
    try:
        with open(MCP_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        config["mcpServers"]["lingtai-kb"]["enabled"] = True
        config["mcpServers"]["lingtai-kb"]["disabled"] = False
        
        with open(MCP_CONFIG, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        return True, "已启用 lingtai-kb 配置"
    except Exception as e:
        return False, str(e)


def _fix_data_source_missing() -> Tuple[bool, str]:
    """修复：重建索引"""
    try:
        if not BUILD_INDEX_SCRIPT.exists():
            return False, f"build_index.py 不存在: {BUILD_INDEX_SCRIPT}"
        
        result = subprocess.run(
            [sys.executable, str(BUILD_INDEX_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(VAULT),
            timeout=60
        )
        
        if result.returncode == 0:
            return True, "索引重建成功"
        else:
            return False, f"重建失败: {result.stderr[:200]}"
    except Exception as e:
        return False, str(e)


def _fix_dependencies_missing(missing: list) -> Tuple[bool, str]:
    """修复：安装依赖"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + missing,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            return True, f"已安装: {', '.join(missing)}"
        else:
            return False, f"安装失败: {result.stderr[:200]}"
    except Exception as e:
        return False, str(e)


def _fix_token_db_corrupted() -> Tuple[bool, str]:
    """修复：重建Token数据库"""
    try:
        # 备份旧文件
        if TOKEN_DB.exists():
            backup = TOKEN_DB.with_suffix(".db.bak")
            TOKEN_DB.rename(backup)
        
        # 创建新数据库
        import sqlite3
        TOKEN_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(TOKEN_DB))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS operation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                saved_tokens INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0.0,
                saved_cost REAL NOT NULL DEFAULT 0.0,
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS counters (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS today_counters (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0,
                date TEXT NOT NULL DEFAULT (date('now','localtime'))
            );
        """)
        conn.close()
        
        return True, "Token数据库已重建"
    except Exception as e:
        return False, str(e)


# ========== 检查函数 ==========

def check_mcp_config() -> CheckResult:
    """检查MCP配置"""
    if not MCP_CONFIG.exists():
        return CheckResult(
            "MCP配置",
            "❌",
            f"mcp.json 不存在: {MCP_CONFIG}",
            "创建 mcp.json 并添加灵识配置",
            fixable=True,
            fix_func=_fix_mcp_config_missing
        )
    
    try:
        with open(MCP_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        servers = config.get("mcpServers", {})
        lingtai_kb = servers.get("lingtai-kb")
        
        if not lingtai_kb:
            # 尝试添加配置
            def fix_add_config():
                try:
                    if "mcpServers" not in config:
                        config["mcpServers"] = {}
                    config["mcpServers"]["lingtai-kb"] = DEFAULT_MCP_CONFIG["mcpServers"]["lingtai-kb"]
                    with open(MCP_CONFIG, "w", encoding="utf-8") as f:
                        json.dump(config, f, indent=2, ensure_ascii=False)
                    return True, "已添加 lingtai-kb 配置"
                except Exception as e:
                    return False, str(e)
            
            return CheckResult(
                "MCP配置",
                "⚠️",
                "mcp.json 中未找到 lingtai-kb 配置",
                "添加 lingtai-kb MCP服务器配置",
                fixable=True,
                fix_func=fix_add_config
            )
        
        if not lingtai_kb.get("enabled", True):
            return CheckResult(
                "MCP配置",
                "⚠️",
                "lingtai-kb 配置已禁用",
                "将 enabled 设置为 true",
                fixable=True,
                fix_func=_fix_mcp_config_disabled
            )
        
        return CheckResult(
            "MCP配置",
            "✅",
            f"lingtai-kb 配置正常 (command: {lingtai_kb.get('command', 'N/A')})"
        )
    
    except Exception as e:
        return CheckResult(
            "MCP配置",
            "❌",
            f"解析 mcp.json 失败: {e}",
            "检查 JSON 格式是否正确"
        )


def check_mcp_server() -> CheckResult:
    """检查MCP服务器"""
    if not MCP_SERVER.exists():
        return CheckResult(
            "MCP服务器",
            "❌",
            f"mcp_server.py 不存在: {MCP_SERVER}",
            "确保灵识模块已正确安装"
        )
    
    try:
        with open(MCP_SERVER, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Phase 2 模块拆分后，主类移到 server.py，mcp_server.py 为薄代理
        server_py = MCP_SERVER.parent / "server.py"
        class_in_server = server_py.exists() and "class LingtaiMCPServer" in server_py.read_text("utf-8")
        
        if class_in_server:
            return CheckResult(
                "MCP服务器",
                "✅",
                f"LingtaiMCPServer 在 server.py 中正常 (mcp_server.py 为薄代理)"
            )
        
        if "class LingtaiMCPServer" in content:
            return CheckResult(
                "MCP服务器",
                "✅",
                f"mcp_server.py 正常 ({len(content)} bytes)"
            )
        
        return CheckResult(
            "MCP服务器",
            "⚠️",
            "mcp_server.py 及 server.py 中均未找到 LingtaiMCPServer 类",
            "模块化拆分后类可能移至 server.py，或文件可能已损坏"
        )
    
    except Exception as e:
        return CheckResult(
            "MCP服务器",
            "❌",
            f"读取 mcp_server.py 失败: {e}",
            "检查文件权限"
        )


def check_data_source() -> CheckResult:
    """检查数据源"""
    if not INDEX_JSON.exists():
        return CheckResult(
            "数据源",
            "❌",
            f"index.json 不存在: {INDEX_JSON}",
            "运行 python .tool/scripts/build_index.py 重建索引",
            fixable=True,
            fix_func=_fix_data_source_missing
        )
    
    try:
        with open(INDEX_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        pages = data.get("pages", [])
        stats = data.get("_stats", {})
        
        if not pages:
            return CheckResult(
                "数据源",
                "⚠️",
                "index.json 中没有页面数据",
                "运行 build_index.py 重建索引",
                fixable=True,
                fix_func=_fix_data_source_missing
            )
        
        return CheckResult(
            "数据源",
            "✅",
            f"index.json 正常 ({len(pages)} 页, {stats.get('total_links', 0)} 链接)"
        )
    
    except Exception as e:
        return CheckResult(
            "数据源",
            "❌",
            f"读取 index.json 失败: {e}",
            "运行 build_index.py 重建索引",
            fixable=True,
            fix_func=_fix_data_source_missing
        )


def check_llm_config() -> CheckResult:
    """检查LLM配置"""
    if not MODELS_JSON.exists():
        return CheckResult(
            "LLM配置",
            "⚠️",
            f"models.json 不存在: {MODELS_JSON}",
            "LLM推理功能将不可用，但基本查询功能正常"
        )
    
    try:
        with open(MODELS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 兼容两种结构：{ "models": [...] }（当前格式）或顶层 [...]（旧式）
        if isinstance(data, dict):
            models = data.get("models", [])
        elif isinstance(data, list):
            models = data
        else:
            models = []

        if not models:
            return CheckResult(
                "LLM配置",
                "⚠️",
                "models.json 中没有模型配置",
                "添加可用的LLM模型配置"
            )
        
        has_key = any(isinstance(m, dict) and m.get("apiKey") for m in models)
        if not has_key:
            return CheckResult(
                "LLM配置",
                "⚠️",
                "models.json 中没有配置API密钥",
                "为模型配置API密钥"
            )
        
        return CheckResult(
            "LLM配置",
            "✅",
            f"LLM配置正常 ({len(models)} 个模型)"
        )
    
    except Exception as e:
        return CheckResult(
            "LLM配置",
            "❌",
            f"读取 models.json 失败: {e}",
            "检查 JSON 格式是否正确"
        )


def check_dependencies() -> CheckResult:
    """检查依赖库"""
    missing = []
    
    try:
        import requests
    except ImportError:
        missing.append("requests")
    
    if missing:
        def fix_deps():
            return _fix_dependencies_missing(missing)
        
        return CheckResult(
            "依赖库",
            "⚠️",
            f"缺少依赖: {', '.join(missing)}",
            f"pip install {' '.join(missing)}",
            fixable=True,
            fix_func=fix_deps
        )
    
    return CheckResult(
        "依赖库",
        "✅",
        "所有依赖已安装"
    )


def check_token_db() -> CheckResult:
    """检查Token数据库"""
    if not TOKEN_DB.exists():
        return CheckResult(
            "Token数据库",
            "⚠️",
            f"token_monitor.db 不存在: {TOKEN_DB}",
            "首次使用时会自动创建",
            fixable=True,
            fix_func=_fix_token_db_corrupted
        )
    
    try:
        import sqlite3
        conn = sqlite3.connect(str(TOKEN_DB))
        cursor = conn.execute("SELECT COUNT(*) FROM operation_log")
        count = cursor.fetchone()[0]
        conn.close()
        
        return CheckResult(
            "Token数据库",
            "✅",
            f"token_monitor.db 正常 ({count} 条记录)"
        )
    
    except Exception as e:
        return CheckResult(
            "Token数据库",
            "⚠️",
            f"token_monitor.db 可能损坏: {e}",
            "删除文件后重新运行会自动创建",
            fixable=True,
            fix_func=_fix_token_db_corrupted
        )


# ========== 主函数 ==========

def run_selfcheck(fix: bool = False) -> dict:
    """
    运行自检
    
    Args:
        fix: 是否自动修复
    """
    print("=" * 60)
    print("灵识自检" + (" + 修复" if fix else ""))
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    checks = [
        check_mcp_config(),
        check_mcp_server(),
        check_data_source(),
        check_llm_config(),
        check_dependencies(),
        check_token_db(),
    ]
    
    # 输出结果
    for check in checks:
        print(check)
        print()
    
    # 统计（修复前）
    passed = sum(1 for c in checks if c.status == "✅")
    warnings = sum(1 for c in checks if c.status == "⚠️")
    errors = sum(1 for c in checks if c.status == "❌")
    
    print("=" * 60)
    print(f"检查结果: ✅{passed} / ⚠️{warnings} / ❌{errors}")
    
    # 自动修复
    if fix and (warnings > 0 or errors > 0):
        print()
        print("🔧 开始自动修复...")
        print()
        
        fixed_count = 0
        for check in checks:
            if check.status != "✅" and check.fixable:
                print(f"  修复 {check.name}...")
                if check.fix():
                    fixed_count += 1
                    print(f"    ✅ {check.fix_detail}")
                else:
                    print(f"    ❌ {check.fix_detail}")
        
        print()
        print(f"修复完成: {fixed_count} 项")
        
        # 重新检查
        print()
        print("🔄 重新检查...")
        print()
        
        checks = [
            check_mcp_config(),
            check_mcp_server(),
            check_data_source(),
            check_llm_config(),
            check_dependencies(),
            check_token_db(),
        ]
        
        for check in checks:
            print(check)
            print()
        
        # 重新统计
        passed = sum(1 for c in checks if c.status == "✅")
        warnings = sum(1 for c in checks if c.status == "⚠️")
        errors = sum(1 for c in checks if c.status == "❌")
        
        print("=" * 60)
        print(f"修复后: ✅{passed} / ⚠️{warnings} / ❌{errors}")
    
    # 结论
    print()
    if errors == 0 and warnings == 0:
        print("结论: 灵识MCP注入链路完全正常")
    elif errors == 0:
        print("结论: 灵识MCP注入链路基本正常（有警告）")
    else:
        print("结论: 存在错误，需要手动修复")
    
    print("=" * 60)
    
    return {
        "passed": passed,
        "warnings": warnings,
        "errors": errors,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail, "fixed": c.fixed} for c in checks]
    }


if __name__ == "__main__":
    fix_mode = "--fix" in sys.argv
    run_selfcheck(fix=fix_mode)
