"""Export the current SQLite content for the Cloudflare Worker build."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "news.db"
OUTPUT_PATH = ROOT / "dist" / "articles.json"


def main() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        articles = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, title, link, description, author, source_name, category,
                       published_at, hot_score, tags, is_featured, source_count,
                       translated_title, translated_desc
                FROM articles
                ORDER BY published_at DESC, id DESC
                """
            )
        ]
        sources = [
            dict(row)
            for row in connection.execute(
                """
                SELECT name, url, category, last_fetched, fetch_success
                FROM sources
                WHERE enabled = 1
                ORDER BY name
                """
            )
        ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles,
        "sources": sources,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Exported {len(articles)} articles and {len(sources)} sources to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
