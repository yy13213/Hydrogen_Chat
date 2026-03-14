"""
文件锁工具：对 .jsonl 和 .json 文件的安全读写操作
依赖 filelock 库
"""
import json
import os
from pathlib import Path
from typing import Any, Callable, List, Optional

from filelock import FileLock


def _lock_path(file_path: str) -> str:
    return file_path + ".lock"


def read_jsonl(file_path: str) -> List[dict]:
    """读取 jsonl 文件，返回记录列表"""
    path = Path(file_path)
    if not path.exists():
        return []
    records = []
    with FileLock(_lock_path(file_path)):
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return records


def write_jsonl_append(file_path: str, record: dict) -> None:
    """追加一条记录到 jsonl 文件"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with FileLock(_lock_path(file_path)):
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_jsonl_all(file_path: str, records: List[dict]) -> None:
    """覆盖写入整个 jsonl 文件"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with FileLock(_lock_path(file_path)):
        with open(file_path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def update_jsonl_record(
    file_path: str,
    match_fn: Callable[[dict], bool],
    update_fn: Callable[[dict], dict],
) -> bool:
    """
    查找满足 match_fn 的第一条记录，用 update_fn 更新后写回。
    返回是否找到并更新了记录。
    """
    with FileLock(_lock_path(file_path)):
        records = []
        found = False
        if Path(file_path).exists():
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        for i, rec in enumerate(records):
            if match_fn(rec):
                records[i] = update_fn(rec)
                found = True
                break
        if found:
            with open(file_path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return found


def read_json(file_path: str) -> Any:
    """读取 json 文件"""
    path = Path(file_path)
    if not path.exists():
        return None
    with FileLock(_lock_path(file_path)):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)


def write_json(file_path: str, data: Any) -> None:
    """写入 json 文件（覆盖）"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with FileLock(_lock_path(file_path)):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def get_file_size_chars(file_path: str) -> int:
    """获取文件字符数"""
    path = Path(file_path)
    if not path.exists():
        return 0
    with open(file_path, "r", encoding="utf-8") as f:
        return len(f.read())
