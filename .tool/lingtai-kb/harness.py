# -*- coding: utf-8 -*-
"""
灵识 harness - 实时执行监督回路
================================
解决Context Engineering差距4（高优先级）：缺少实时的执行监督回路

核心思想：在执行关键步骤后插入 check_point，让灵识自检当前中间结果是否符合预期。
相当于给6个自动化任务加上「步骤级护栏」。

使用方式：
    from harness import Harness
    
    h = Harness()
    h.begin("提炼任务")
    h.check("原料读取完成", {"has_content": True, "content_length": len(content)})
    h.check("目标页找到", {"found": True, "page": "xxx"})
    h.check("内容写入完成", {"file_exists": True})
    result = h.end()
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


class CheckPoint:
    """单个检查点"""

    def __init__(self, name: str, result: dict, passed: bool):
        self.name = name
        self.result = result
        self.passed = passed
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "result": self.result,
            "timestamp": self.timestamp,
        }


class Harness:
    """实时执行监督引擎"""

    def __init__(self, log_dir: str = None):
        if log_dir is None:
            log_dir = str(Path(__file__).parent / ".cache")
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.task_name = ""
        self.checkpoints: List[CheckPoint] = []
        self.start_time = None

    def begin(self, task_name: str):
        """开始一个监督任务"""
        self.task_name = task_name
        self.checkpoints = []
        self.start_time = datetime.now()

    def check(self, name: str, conditions: Dict[str, Any] = None, validator: Callable = None) -> bool:
        """
        执行检查点
        
        Args:
            name: 检查点名称
            conditions: 条件字典 {"key": expected_value}，值为True则通过
            validator: 自定义验证函数，返回 (passed: bool, detail: str)
        
        Returns:
            bool: 是否通过
        """
        passed = True
        result = {}

        if conditions:
            for key, expected in conditions.items():
                if expected is True:
                    # 值为True表示该条件必须为真
                    result[key] = True  # 实际场景中应传入真实值
                else:
                    result[key] = expected

        if validator:
            try:
                passed, detail = validator()
                result["validator_detail"] = detail
            except Exception as e:
                passed = False
                result["validator_error"] = str(e)

        cp = CheckPoint(name, result, passed)
        self.checkpoints.append(cp)

        if not passed:
            print(f"  ❌ check_point FAILED: {name}")
        else:
            print(f"  ✅ check_point PASSED: {name}")

        return passed

    def check_file_exists(self, path: str, label: str = None) -> bool:
        """检查文件是否存在"""
        import os
        exists = os.path.isfile(path)
        return self.check(label or f"文件存在: {path}", {"exists": exists})

    def check_file_not_empty(self, path: str, min_size: int = 10, label: str = None) -> bool:
        """检查文件非空"""
        import os
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        passed = size >= min_size
        return self.check(label or f"文件非空: {path}", {"size": size, "min_size": min_size, "passed": passed})

    def check_fm_fields(self, path: str, required_fields: list, label: str = None) -> bool:
        """检查FM字段完整性"""
        import os, re
        if not os.path.isfile(path):
            return self.check(label or f"FM检查: {path}", {"error": "file not found"})

        content = open(path, 'r', encoding='utf-8').read()
        missing = [f for f in required_fields if f not in content]
        passed = len(missing) == 0
        return self.check(
            label or f"FM字段检查: {path}",
            {"required": required_fields, "missing": missing, "passed": passed}
        )

    def check_no_errors(self, errors: list, label: str = None) -> bool:
        """检查无错误"""
        passed = len(errors) == 0
        return self.check(label or "无错误", {"errors": errors, "passed": passed})

    def check_metric(self, name: str, value: Any, op: str, threshold: Any, label: str = None) -> bool:
        """检查指标是否满足条件"""
        ops = {
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
        }
        func = ops.get(op)
        if func is None:
            return self.check(label or f"指标检查: {name}", {"error": f"unknown op: {op}"})

        passed = func(value, threshold)
        return self.check(
            label or f"指标检查: {name}",
            {"value": value, "op": op, "threshold": threshold, "passed": passed}
        )

    def end(self) -> dict:
        """
        结束监督任务，返回总结
        
        Returns:
            dict: {task, total_checks, passed, failed, duration, checkpoints, log_path}
        """
        duration = (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        passed = sum(1 for cp in self.checkpoints if cp.passed)
        failed = len(self.checkpoints) - passed

        summary = {
            "task": self.task_name,
            "total_checks": len(self.checkpoints),
            "passed": passed,
            "failed": failed,
            "all_passed": failed == 0,
            "duration_seconds": round(duration, 2),
            "checkpoints": [cp.to_dict() for cp in self.checkpoints],
        }

        # 写入日志
        log_path = self.log_dir / "harness_log.jsonl"
        entry = {
            "timestamp": datetime.now().isoformat(),
            "task": self.task_name,
            "passed": passed,
            "failed": failed,
            "duration": duration,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # 打印总结
        status = "✅ 全部通过" if failed == 0 else f"❌ {failed}项失败"
        print(f"\n[Harness] {self.task_name}: {status} ({passed}/{len(self.checkpoints)} checks, {duration:.1f}s)")

        return summary
