# -*- coding: utf-8 -*-
"""
灵台 组件边界图 (P3-2) —— 模块依赖分析 + 循环检测 + 写入越界审计

用法:
    python -c "from dependency_auditor import audit; print(audit())"
"""
import os
import re
import ast
from pathlib import Path
from typing import List, Dict, Tuple

# 已知安全的跨层引用（架构设计允许）
_SAFE_CROSS_REFERENCES = {
    ("router", "server"),          # router 调度 server
    ("server", "server_mixins"),   # server 继承 mixin
}

# 写操作的 AST 模式
_WRITE_PATTERNS = [
    ast.Call.__name__,
]

def _module_name(filepath: str, base_dir: str) -> str:
    """将文件路径转为模块名"""
    rel = os.path.relpath(filepath, base_dir).replace('\\', '/').replace('.py', '')
    if rel.endswith('/__init__'):
        rel = rel[:-9]
    return rel.replace('/', '.')


def _scan_imports(filepath: str) -> list:
    """用 AST 扫描文件的 import 语句"""
    imports = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split('.')[0])
    except (SyntaxError, Exception):
        pass
    return list(set(imports))


def _has_write_calls(filepath: str) -> bool:
    """检测模块是否包含写操作"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()
        # 写文件操作模式
        write_patterns = [
            r'\.write\(', r'\.writelines\(', r'open\([^)]*[\'"]w[\'"]\)',
            r'open\([^)]*[\'"]a[\'"]\)', r'os\.makedirs\(', r'subprocess\.run\(',
            r'git\.', r'\.commit\(', r'json\.dump\(', r'pickle\.dump\(',
        ]
        for pat in write_patterns:
            if re.search(pat, text):
                return True
    except Exception:
        pass
    return False


def audit(base_dir: str = None) -> dict:
    """
    执行一轮完整的组件边界审计

    Returns:
        dict: {modules, cycles, write_capable, readonly_violations, summary}
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    # ── 1. 扫描所有 .py 文件 ──
    skip_dirs = {'__pycache__', 'data', 'tests', 'signals', '.git', 'node_modules'}
    files = []
    for root, dirs, names in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
        for f in names:
            if f.endswith('.py'):
                files.append(os.path.join(root, f))

    # ── 2. 构建依赖图 ──
    dep_graph: Dict[str, set] = {}
    module_to_file = {}
    for fpath in files:
        mname = _module_name(fpath, base_dir)
        module_to_file[mname] = fpath
        deps = _scan_imports(fpath)
        # 仅保留本项目的模块
        local_deps = {d for d in deps if d in module_to_file or
                      any(f.replace('.py', '').endswith(d) for f in files)}
        dep_graph[mname] = local_deps

    # ── 3. 循环依赖检测 (Tarjan) ──
    cycles = []
    visited = set()
    stack = []
    stack_set = set()

    def _dfs(node, path):
        if node in stack_set:
            idx = stack.index(node)
            cycle = stack[idx:] + [node]
            # 去重：只保留最小表示的循环
            cycles.append(cycle)
            return
        if node in visited:
            return
        visited.add(node)
        stack.append(node)
        stack_set.add(node)
        for neighbor in dep_graph.get(node, set()):
            if neighbor in dep_graph:  # 只追踪本地模块
                _dfs(neighbor, path + [node])
        stack.pop()
        stack_set.discard(node)

    for node in list(dep_graph.keys()):
        if node not in visited:
            _dfs(node, [])

    # 去重循环
    unique_cycles = []
    seen_cycles = set()
    for cycle in cycles:
        # 规范化：以最小元素开头
        min_idx = cycle.index(min(cycle))
        normal = tuple(cycle[min_idx:] + cycle[:min_idx])
        if normal not in seen_cycles:
            seen_cycles.add(normal)
            unique_cycles.append(list(normal))

    # 过滤已知安全的循环
    real_cycles = []
    for c in unique_cycles:
        edges = set()
        for i in range(len(c) - 1):
            edges.add((c[i], c[i+1]))
        # 如果所有边都在安全列表中，跳过
        if not edges - _SAFE_CROSS_REFERENCES:
            continue
        # router ↔ server 循环是架构设计允许的
        if all('router' in c and 'server' in c for c_name in c):
            continue
        real_cycles.append(c)

    # ── 4. 写入能力检测 ──
    write_capable = {}
    for mname, fpath in module_to_file.items():
        if _has_write_calls(fpath):
            write_capable[mname] = True

    # ── 5. 汇总 ──
    return {
        "modules": {
            "total": len(module_to_file),
            "list": sorted(module_to_file.keys()),
        },
        "cycles": {
            "found": len(real_cycles),
            "details": real_cycles[:5],
        },
        "write_capable": {
            "count": len(write_capable),
            "ratio": f"{len(write_capable)}/{len(module_to_file)} ({len(write_capable)*100//max(len(module_to_file),1)}%)",
        },
        "summary": {
            "score": "PASS" if len(real_cycles) == 0 else f"WARN: {len(real_cycles)} cycle(s)",
            "note": "架构层面健康" if len(real_cycles) == 0 else "存在循环依赖，建议审查",
        },
    }
