"""手动执行文章数据质量修复。正式逻辑位于 backend/db.py。"""
import asyncio
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from db import DB_PATH, calculate_hot_scores, repair_article_data


async def main():
    print(f"数据库：{DB_PATH}")
    result = await repair_article_data()
    updates = await calculate_hot_scores()
    print(f"source_count 修复：{result['source_count_fixed']} 篇")
    print(f"未来时间修复：{result['future_dates_fixed']} 篇")
    print(f"热点分数重算：{len(updates or [])} 篇")


if __name__ == "__main__":
    asyncio.run(main())
