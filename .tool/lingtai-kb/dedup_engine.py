# -*- coding: utf-8 -*-
"""
灵台原料去重引擎（T1 确定性）
============================
三层检测，零LLM消耗：
- L1: SHA-256 正文哈希（精确去重）
- L2: Levenshtein 文件名编辑距离（标题相似）
- L3: SimHash 64bit 正文指纹（内容相似）

持久化：原料/.dedup_cache.json
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional, Tuple


class DedupEngine:
    """原料三层去重引擎"""
    
    def __init__(self, vault_path: str = None):
        """
        初始化去重引擎
        
        Args:
            vault_path: 灵台仓库根目录
        """
        if vault_path is None:
            vault_path = os.environ.get("LINGTAI_VAULT", r".")
        self.vault_path = vault_path
        self.raw_dir = os.path.join(vault_path, "原料")
        self.cache_file = os.path.join(self.raw_dir, ".dedup_cache.json")
        
        # 加载或初始化缓存
        self.cache = self._load_cache()
    
    def _load_cache(self) -> dict:
        """加载去重缓存"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        
        return {
            "version": 1,       # 缓存结构版本，用于向前兼容
            "sha256": {},      # {hash: [file_path, ...]}
            "simhash": {},     # {file_path: hash_64bit}
            "filenames": {},   # {file_path: filename_stem}
        }
    
    def _save_cache(self):
        """保存去重缓存"""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    
    def _strip_frontmatter(self, content: str) -> str:
        """去掉 YAML frontmatter"""
        return re.sub(r'^---\s*\n.*?\n---\s*\n?', '', content, count=1, flags=re.DOTALL)
    
    def _body_hash(self, content: str) -> str:
        """L1: 正文 SHA-256"""
        body = self._strip_frontmatter(content).strip()
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
    
    def _filename_stem(self, file_path: str) -> str:
        """提取文件名（不含扩展名）"""
        return Path(file_path).stem
    
    def _extract_title(self, content: str) -> str:
        """从正文首行提取标题（用于 L2 文件名太短或哈希时兜底）"""
        body = self._strip_frontmatter(content).strip()
        # 取第一行非空正文，去掉 markdown 标记
        for line in body.split('\n'):
            line = line.strip()
            if line:
                # 去掉 # 标题标记、加粗/斜体等
                line = re.sub(r'^#+\s*', '', line)
                line = re.sub(r'[\*_~`]', '', line)
                return line[:60]  # 截断过长标题
        return ''
    
    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        """L2: 编辑距离"""
        if len(s1) < len(s2):
            return DedupEngine._levenshtein(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        prev_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row
        
        return prev_row[-1]
    
    @staticmethod
    def _simhash(text: str, bits: int = 64) -> int:
        """L3: SimHash 64bit 内容指纹（自动剥离 frontmatter）"""
        # 剥离 frontmatter，避免日期/处理状态干扰指纹
        body = re.sub(r'^---\s*\n.*?\n---\s*\n?', '', text, count=1, flags=re.DOTALL)
        body = body.strip()
        if not body:
            return 0
        # 字符 3-gram 作为特征
        shingles = set()
        for i in range(len(body) - 2):
            shingles.add(body[i:i+3])
        
        if not shingles:
            return 0
        
        # 64位累加器
        v = [0] * bits
        for shingle in shingles:
            h = int(hashlib.md5(shingle.encode("utf-8")).hexdigest(), 16)
            for i in range(bits):
                if h & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1
        
        # 生成指纹
        fingerprint = 0
        for i in range(bits):
            if v[i] > 0:
                fingerprint |= (1 << i)
        
        return fingerprint
    
    @staticmethod
    def _hamming_distance(hash1: int, hash2: int) -> int:
        """汉明距离"""
        return bin(hash1 ^ hash2).count("1")
    
    def check(self, content: str, filename: str = None, source_url: str = None) -> dict:
        """
        三层去重检查
        
        Args:
            content: 文件内容（含frontmatter）
            filename: 文件名（可选，用于L2）
            source_url: 来源URL（可选，预留）
        
        Returns:
            dict: {
                "is_dup": bool,
                "match": str or None,  # 匹配的文件路径
                "method": str or None,  # "sha256" | "levenshtein" | "simhash"
                "confidence": float,   # 置信度 0-1
            }
        """
        # L1: SHA-256 精确去重
        body_hash = self._body_hash(content)
        if body_hash in self.cache["sha256"]:
            matches = self.cache["sha256"][body_hash]
            if matches:
                return {
                    "is_dup": True,
                    "match": matches[0],
                    "method": "sha256",
                    "confidence": 1.0,
                }
        
        # L2: 标题编辑距离
        # 优先用文件名，若文件名是哈希/过短则从正文首行提取标题
        l2_title = None
        if filename:
            stem = Path(filename).stem
            # 文件名是哈希（≤12字符）或含时间戳 → 改用正文标题
            if len(stem) <= 12 or re.search(r'\d{8,}', stem):
                l2_title = self._extract_title(content)
            else:
                l2_title = stem
        if not l2_title:
            l2_title = self._extract_title(content)
        
        if l2_title and len(l2_title) > 2:
            for existing_path, existing_stem in self.cache["filenames"].items():
                dist = self._levenshtein(l2_title, existing_stem)
                if dist <= 3:
                    return {
                        "is_dup": True,
                        "match": existing_path,
                        "method": "levenshtein",
                        "confidence": max(0, 1 - dist / 10),
                    }
        
        # L3: SimHash 内容指纹
        body = self._strip_frontmatter(content).strip()
        body_len = len(body)
        
        # 短文本跳过L3（<50字容易误判）
        if body_len >= 50:
            simhash = self._simhash(content)  # _simhash 内部会剥离 frontmatter
            for existing_path, existing_hash in self.cache["simhash"].items():
                distance = self._hamming_distance(simhash, existing_hash)
                # 64bit 汉明距离阈值：<200字短文用5，≥200字长文用10
                threshold = 5 if body_len < 200 else 10
                if distance <= threshold:
                    return {
                        "is_dup": True,
                        "match": existing_path,
                        "method": "simhash",
                        "confidence": max(0, 1 - distance / 64),
                        "distance": distance,
                    }
        
        # 未命中
        return {
            "is_dup": False,
            "match": None,
            "method": None,
            "confidence": 0.0,
        }
    
    def register(self, file_path: str, content: str):
        """
        注册文件到去重索引（去重检查通过后调用）
        
        Args:
            file_path: 文件相对路径（如 "原料/追问.md"）
            content: 文件内容
        """
        body_hash = self._body_hash(content)
        stem = Path(file_path).stem
        
        # 注册 SHA-256
        if body_hash not in self.cache["sha256"]:
            self.cache["sha256"][body_hash] = []
        if file_path not in self.cache["sha256"][body_hash]:
            self.cache["sha256"][body_hash].append(file_path)
        
        # 注册 SimHash（剥离 frontmatter 后计算）
        body = self._strip_frontmatter(content)
        simhash = self._simhash(body)
        self.cache["simhash"][file_path] = simhash
        
        # 注册文件名
        self.cache["filenames"][file_path] = stem
        
        self._save_cache()
    
    def build_index(self, force: bool = False) -> dict:
        """
        全量重建去重索引
        
        Args:
            force: 强制重建（忽略已有缓存）
        
        Returns:
            dict: 重建统计
        """
        if force:
            self.cache = {"version": 1, "sha256": {}, "simhash": {}, "filenames": {}}
        
        raw_dir = Path(self.raw_dir)
        if not raw_dir.exists():
            return {"error": f"原料目录不存在: {self.raw_dir}"}
        
        files = list(raw_dir.glob("*.md"))
        registered = 0
        skipped = 0
        
        for f in files:
            try:
                content = f.read_text(encoding="utf-8")
                rel_path = f"原料/{f.name}"
                
                # 检查是否已注册
                if rel_path in self.cache["simhash"] and not force:
                    skipped += 1
                    continue
                
                self.register(rel_path, content)
                registered += 1
            except Exception:
                continue
        
        return {
            "total": len(files),
            "registered": registered,
            "skipped": skipped,
            "sha256_groups": len(self.cache["sha256"]),
        }
    
    def scan_duplicates(self) -> list:
        """
        扫描所有重复项（用于报告）
        
        Returns:
            list: 重复项列表
        """
        duplicates = []
        
        # 扫描 SHA-256 重复
        for hash_val, files in self.cache["sha256"].items():
            if len(files) > 1:
                duplicates.append({
                    "method": "sha256",
                    "files": files,
                    "confidence": 1.0,
                })
        
        # 扫描 SimHash 近重复（需要逐对比较）
        simhash_items = list(self.cache["simhash"].items())
        for i, (path1, hash1) in enumerate(simhash_items):
            for path2, hash2 in simhash_items[i+1:]:
                distance = self._hamming_distance(hash1, hash2)
                if distance <= 10:
                    duplicates.append({
                        "method": "simhash",
                        "files": [path1, path2],
                        "confidence": max(0, 1 - distance / 64),
                        "distance": distance,
                    })
        
        # 扫描 L2 Levenshtein 标题相似
        filenames = list(self.cache["filenames"].items())
        for i, (path1, stem1) in enumerate(filenames):
            for path2, stem2 in filenames[i+1:]:
                dist = self._levenshtein(stem1, stem2)
                if dist <= 3 and len(stem1) > 2 and len(stem2) > 2:
                    duplicates.append({
                        "method": "levenshtein",
                        "files": [path1, path2],
                        "confidence": max(0, 1 - dist / 10),
                        "distance": dist,
                    })
        
        return duplicates


def create_dedup_engine(vault_path: str = None) -> DedupEngine:
    """工厂函数"""
    return DedupEngine(vault_path)


if __name__ == "__main__":
    # 测试
    engine = DedupEngine()
    
    # 全量建库
    print("Building index...")
    stats = engine.build_index(force=True)
    print(f"Stats: {stats}")
    
    # 扫描重复
    print("\nScanning duplicates...")
    dups = engine.scan_duplicates()
    print(f"Found {len(dups)} duplicate groups")
    
    for dup in dups[:10]:
        print(f"  [{dup['method']}] {dup['files']}")
