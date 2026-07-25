# -*- coding: utf-8 -*-
"""
灵台MCP - Obsidian CLI 封装
============================
封装 obsidian CLI 命令，提供稳定的文件操作接口。
CLI 不可用时自动回退到 Python 文件操作。
"""

import os
import subprocess
import json
from typing import Optional, List, Dict


class ObsidianCLI:
    """Obsidian CLI 封装"""

    VAULT = r"."
    _available = None

    @classmethod
    def _check_available(cls) -> bool:
        if cls._available is not None:
            return cls._available
        try:
            r = subprocess.run(
                ["obsidian", "version"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=5
            )
            cls._available = r.returncode == 0
        except Exception:
            cls._available = False
        return cls._available

    @classmethod
    def _run(cls, *args: str, vault: str = None) -> str:
        cmd = ["obsidian"]
        if vault:
            cmd.append(f"vault={vault}")
        cmd.extend(args)
        try:
            r = subprocess.run(
                " ".join(cmd), shell=True, capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=10
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @classmethod
    def _run_json(cls, *args: str, vault: str = None) -> Optional[dict | list]:
        output = cls._run(*args, vault=vault)
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output

    @classmethod
    def is_available(cls) -> bool:
        return cls._check_available()

    # === 文件读写 ===

    @classmethod
    def read(cls, path: str) -> str:
        if cls._check_available():
            result = cls._run("read", f'path="{path}"')
            if result:
                return result
        full = os.path.join(cls.VAULT, path)
        with open(full, "r", encoding="utf-8") as f:
            return f.read()

    @classmethod
    def create(cls, name: str, content: str = "", folder: str = None) -> bool:
        if folder:
            path = f"{folder}/{name}.md"
        else:
            path = name
        if cls._check_available():
            result = cls._run("create", f'path="{path}"', f'content="{content}"', "silent")
            return bool(result)
        full = os.path.join(cls.VAULT, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return True

    @classmethod
    def append(cls, path: str, content: str) -> bool:
        if cls._check_available():
            result = cls._run("append", f'path="{path}"', f'content="{content}"')
            return bool(result)
        full = os.path.join(cls.VAULT, path)
        with open(full, "a", encoding="utf-8") as f:
            f.write(content)
        return True

    @classmethod
    def delete(cls, path: str) -> bool:
        if cls._check_available():
            result = cls._run("delete", f'path="{path}"')
            return bool(result)
        full = os.path.join(cls.VAULT, path)
        if os.path.isfile(full):
            os.remove(full)
            return True
        return False

    # === 搜索 ===

    @classmethod
    def search(cls, query: str, folder: str = None, limit: int = 20) -> str:
        args = ["search", f'query="{query}"', f"limit={limit}"]
        if folder:
            args.append(f'path="{folder}"')
        return cls._run(*args) if cls._check_available() else ""

    @classmethod
    def search_json(cls, query: str, folder: str = None, limit: int = 20) -> Optional[list]:
        args = ["search", f'query="{query}"', f"limit={limit}", "format=json"]
        if folder:
            args.append(f'path="{folder}"')
        return cls._run_json(*args) if cls._check_available() else None

    # === 链接与标签 ===

    @classmethod
    def backlinks(cls, path: str, counts: bool = False) -> str:
        args = ["backlinks", f'path="{path}"']
        if counts:
            args.append("counts")
        return cls._run(*args) if cls._check_available() else ""

    @classmethod
    def backlinks_json(cls, path: str) -> Optional[list]:
        return cls._run_json("backlinks", f'path="{path}"', "format=json") if cls._check_available() else None

    @classmethod
    def links(cls, path: str) -> str:
        return cls._run("links", f'path="{path}"') if cls._check_available() else ""

    @classmethod
    def tags(cls, counts: bool = False) -> str:
        args = ["tags"]
        if counts:
            args.append("counts")
        return cls._run(*args) if cls._check_available() else ""

    @classmethod
    def unresolved(cls) -> Optional[list]:
        return cls._run_json("unresolved", "format=json") if cls._check_available() else None

    # === 属性(FM)操作 ===

    @classmethod
    def property_read(cls, name: str, path: str) -> str:
        return cls._run("property:read", f'name="{name}"', f'path="{path}"') if cls._check_available() else ""

    @classmethod
    def property_set(cls, name: str, value: str, path: str, prop_type: str = "text") -> bool:
        result = cls._run(
            "property:set", f'name="{name}"', f'value="{value}"',
            f'path="{path}"', f'type={prop_type}'
        ) if cls._check_available() else ""
        return bool(result)

    @classmethod
    def properties(cls, path: str = None) -> str:
        args = ["properties"]
        if path:
            args.append(f'path="{path}"')
        return cls._run(*args) if cls._check_available() else ""

    # === 文件列表 ===

    @classmethod
    def files(cls, folder: str = None, ext: str = "md") -> str:
        args = ["files"]
        if folder:
            args.append(f'folder="{folder}"')
        if ext:
            args.append(f'ext={ext}')
        return cls._run(*args) if cls._check_available() else ""

    @classmethod
    def orphans(cls) -> str:
        return cls._run("orphans") if cls._check_available() else ""

    @classmethod
    def deadends(cls) -> str:
        return cls._run("deadends") if cls._check_available() else ""

    # === 词汇统计 ===

    @classmethod
    def wordcount(cls, path: str) -> str:
        return cls._run("wordcount", f'path="{path}"') if cls._check_available() else ""
