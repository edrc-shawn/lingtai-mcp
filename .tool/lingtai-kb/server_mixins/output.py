# -*- coding: utf-8 -*-
"""输出 mixin — 知识到作品的发布流水线"""

import os
from datetime import datetime
from decorators import tool


class OutputMixin:
    """输出管理：作品/ 目录的创建与查询"""

    @tool(readonly=True, write=False, category="output", system=False)
    def output_list(self, platform: str = None) -> dict:
        """列出作品目录中的产出物"""
        vault = self.vault_path
        root = vault + "/作品"
        if not os.path.isdir(root):
            return {"error": "作品/ directory not found"}

        names = ["公众号", "小红书", "抖音", "哔哩哔哩", "配图"]
        if platform:
            names = [p for p in names if p == platform]

        result = {}
        total = 0
        for name in names:
            d = root + "/" + name
            if not os.path.isdir(d):
                result[name] = {"count": 0, "files": []}
                continue
            files = []
            for dirpath, _, filenames in os.walk(d):
                for fn in filenames:
                    if not fn.endswith(".md"):
                        continue
                    fp = dirpath + "/" + fn
                    rel = fp.replace(root + "/", "")
                    title = ""
                    try:
                        with open(fp, "r", encoding="utf-8") as fh:
                            head = fh.read(500)
                        for line in head.split("\n"):
                            if line.startswith("标题: "):
                                title = line.replace("标题: ", "").strip().strip("'\"")
                                break
                    except Exception:
                        title = fn.replace(".md", "")
                    files.append({"path": rel, "title": title})
            result[name] = {"count": len(files), "files": files[:20]}
            total += len(files)

        # root files
        root_files = []
        for fn in os.listdir(root):
            if fn.endswith(".md"):
                root_files.append({"path": fn, "title": fn.replace(".md", "")})
        if root_files:
            result["_root"] = {"count": len(root_files), "files": root_files}
            total += len(root_files)

        return {"total": total, "platforms": result, "filter": platform}

    @tool(readonly=False, write=True, category="output", system=False)
    def output_publish(self, source: str, title: str, platform: str = "公众号",
                       content: str = "", cover: str = "") -> dict:
        """在作品/{platform}/ 下生成发布文件"""
        valid = {"公众号", "小红书", "抖音", "哔哩哔哩"}
        if platform not in valid:
            return {"success": False, "error": "无效平台: " + platform}
        if not title.strip():
            return {"success": False, "error": "标题不能为空"}

        safe = title.replace("/", "·").replace("\\", "·").replace(":", "：")
        d = self.vault_path + "/作品/" + platform
        os.makedirs(d, exist_ok=True)

        fp = d + "/" + safe + ".md"
        if os.path.exists(fp):
            return {"success": False, "error": "作品已存在: " + fp}

        now = datetime.now().strftime("%Y-%m-%d")
        src = source.replace("\\", "/")
        cv = "\n封面: " + cover if cover else ""

        fm = "---\n标题: " + title + "\n日期: " + now + "\n来源: " + src + "\n类型: " + platform + cv + "\n---\n\n"
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(fm + content)

        return {"success": True, "path": "作品/" + platform + "/" + safe + ".md",
                "title": title, "platform": platform,
                "source": src, "char_count": len(content)}