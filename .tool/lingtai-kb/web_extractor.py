# -*- coding: utf-8 -*-
"""
灵台MCP - 网页内容提取（Defuddle）
====================================
从URL提取干净的Markdown内容，作为原料。
自动去除导航、广告、杂乱内容。
"""

import os
import subprocess
import json
from pathlib import Path


class WebExtractor:
    """网页内容提取器"""

    VAULT = r"."
    RAW_DIR = os.path.join(VAULT, "原料")
    _available = None

    @classmethod
    def _check_available(cls) -> bool:
        if cls._available is not None:
            return cls._available
        try:
            r = subprocess.run(
                "defuddle --version", shell=True,
                capture_output=True, text=True, timeout=5
            )
            cls._available = r.returncode == 0
        except Exception:
            cls._available = False
        return cls._available

    @classmethod
    def is_available(cls) -> bool:
        return cls._check_available()

    @classmethod
    def fetch_markdown(cls, url: str) -> str:
        if not cls._check_available():
            return ""
        try:
            r = subprocess.run(
                f"defuddle parse {url} --md", shell=True,
                capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=30
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @classmethod
    def fetch_metadata(cls, url: str, prop: str = "title") -> str:
        if not cls._check_available():
            return ""
        try:
            r = subprocess.run(
                f"defuddle parse {url} -p {prop}", shell=True,
                capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=15
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @classmethod
    def fetch_json(cls, url: str) -> dict | None:
        if not cls._check_available():
            return None
        try:
            r = subprocess.run(
                f"defuddle parse {url} --json", shell=True,
                capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=30
            )
            return json.loads(r.stdout) if r.stdout.strip() else None
        except Exception:
            return None

    @classmethod
    def url_to_raw(cls, url: str, filename: str = None) -> str:
        """从URL提取内容并保存为原料文件"""
        content = cls.fetch_markdown(url)
        if not content:
            return ""

        if not filename:
            title = cls.fetch_metadata(url, "title")
            if not title:
                title = url.split("/")[-1][:30]
            filename = f"{title}.md"

        if not filename.endswith(".md"):
            filename += ".md"

        raw_path = os.path.join(cls.RAW_DIR, filename)
        frontmatter = f"---\n处理状态: 待提炼\n来源: {url}\n---\n\n"

        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(frontmatter + content)

        return raw_path
