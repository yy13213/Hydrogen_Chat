"""
数据集清洗脚本 - 准备上传 HuggingFace
- image_path 改为相对路径 images/xxx.ext（绝对路径 → 取文件名）
- 删除 source_file、generated_at 字段
- 输出到 data_collection/dataset/dataset_clean.jsonl
"""

import json
from pathlib import Path

INPUT_FILE  = Path(__file__).parent / "data_collection" / "dataset" / "dataset.jsonl"
OUTPUT_FILE = Path(__file__).parent / "data_collection" / "dataset" / "dataset_clean.jsonl"


def clean_record(rec: dict) -> dict:
    result = {}

    # image_path：绝对路径 → 相对路径 images/xxx.ext；null 保持 null
    raw_path = rec.get("image_path")
    if raw_path:
        result["image_path"] = "images/" + Path(raw_path).name
    else:
        result["image_path"] = None

    result["prompt"]              = rec.get("prompt", "")
    result["ground_truth"]        = rec.get("ground_truth", "")
    result["reference_guideline"] = rec.get("reference_guideline", "")

    # source_file 和 generated_at 不写入
    return result


def main():
    if not INPUT_FILE.exists():
        print(f"输入文件不存在：{INPUT_FILE}")
        return

    total = 0
    written = 0

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(INPUT_FILE, "r", encoding="utf-8") as fin, \
         open(OUTPUT_FILE, "w", encoding="utf-8") as fout:

        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
                cleaned = clean_record(rec)
                fout.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
                written += 1
            except json.JSONDecodeError as e:
                print(f"  跳过第 {total} 行（JSON 解析失败）：{e}")

    print(f"完成：共读取 {total} 条，成功写入 {written} 条")
    print(f"输出文件：{OUTPUT_FILE}")


if __name__ == "__main__":
    main()
