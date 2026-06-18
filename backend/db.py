"""
新闻热点 - 数据库模型和操作。
SQLite 存储 RSS 文章、分类、日报、热点分数、精选标记。
启用 WAL 模式提高并发性能。
"""

import aiosqlite
import os
import sys
from datetime import datetime, date, timezone
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = _PROJECT_ROOT
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "news.db")

# Add backend to sys.path for imports from scraper
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _parse_published_at(pub_str: str) -> datetime:
    """统一解析 published_at 字符串为 naive datetime（UTC）"""
    if not pub_str:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        cleaned = pub_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        # 如果有时区信息，转为 UTC naive datetime
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _hours_ago_naive(dt: datetime) -> float:
    """计算 naive datetime 距今的小时数（假设 dt 是 UTC）"""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    diff = now_utc - dt
    return max(0.0, diff.total_seconds() / 3600)


def calculate_hot_score(published_at_str: str, source_count: int) -> float:
    """计算单篇文章热点分数（统一算法）"""
    base = 10.0
    source_bonus = (max(1, source_count) - 1) * 5.0
    pub_time = _parse_published_at(published_at_str)
    hours_ago = _hours_ago_naive(pub_time)
    decay = hours_ago * 0.5
    return max(0.0, base + source_bonus - decay)


def _split_source_names(source_names: str) -> list[str]:
    """拆分并去重信源名，保留原顺序。"""
    unique_names = []
    seen = set()
    for name in (source_names or "").split(","):
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_names.append(cleaned)
    return unique_names


async def repair_article_data():
    """修复可由现有字段确定的数据质量问题，可安全重复执行。"""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    now_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, source_name, source_count, published_at, fetched_at FROM articles"
        ) as cursor:
            rows = await cursor.fetchall()

        source_updates = []
        date_updates = []
        for row in rows:
            source_names = _split_source_names(row["source_name"] or "")
            normalized_source_names = ", ".join(source_names)
            normalized_count = max(1, len(source_names))
            if (
                normalized_source_names != (row["source_name"] or "")
                or normalized_count != (row["source_count"] or 1)
            ):
                source_updates.append((normalized_source_names, normalized_count, row["id"]))

            published_at = _parse_published_at(row["published_at"] or "")
            if published_at > now_utc:
                fallback = _parse_published_at(row["fetched_at"] or now_str)
                corrected = min(fallback, now_utc).strftime("%Y-%m-%d %H:%M:%S")
                date_updates.append((corrected, row["id"]))

        if source_updates:
            await db.executemany(
                "UPDATE articles SET source_name=?, source_count=? WHERE id=?",
                source_updates,
            )
        if date_updates:
            await db.executemany(
                "UPDATE articles SET published_at=? WHERE id=?",
                date_updates,
            )
        if source_updates or date_updates:
            await db.commit()

    return {
        "source_count_fixed": len(source_updates),
        "future_dates_fixed": len(date_updates),
    }


async def init_db():
    """初始化数据库表，启用 WAL 模式"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA cache_size=-64000")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                category TEXT DEFAULT 'general',
                enabled BOOLEAN DEFAULT 1,
                last_fetched TIMESTAMP,
                fetch_success BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                description TEXT,
                content TEXT,
                author TEXT,
                source_name TEXT,
                category TEXT,
                published_at TIMESTAMP,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                hot_score REAL DEFAULT 0,
                tags TEXT DEFAULT '',
                is_featured BOOLEAN DEFAULT 0,
                source_count INTEGER DEFAULT 1,
                translated_title TEXT,
                translated_desc TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                digest_date DATE NOT NULL UNIQUE,
                content TEXT NOT NULL,
                article_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 索引
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_featured ON articles(is_featured)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_hot ON articles(hot_score DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_articles_link ON articles(link)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sources_url ON sources(url)")
        
        # 迁移：添加翻译字段（如果不存在）
        try:
            await db.execute("ALTER TABLE articles ADD COLUMN translated_title TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE articles ADD COLUMN translated_desc TEXT")
        except Exception:
            pass
        # 迁移：添加信源健康状态字段
        try:
            await db.execute("ALTER TABLE sources ADD COLUMN last_fetched TIMESTAMP")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE sources ADD COLUMN fetch_success BOOLEAN")
        except Exception:
            pass
        
        await db.commit()

    await repair_article_data()


async def add_source(name: str, url: str, category: str = "general"):
    """添加或更新 RSS 信源"""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO sources (name, url, category) VALUES (?, ?, ?)",
                (name, url, category)
            )
        except aiosqlite.IntegrityError:
            await db.execute(
                "UPDATE sources SET name=?, category=? WHERE url=?",
                (name, category, url)
            )
        await db.commit()


async def update_source_health(url: str, success: bool):
    """更新信源健康状态"""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "UPDATE sources SET last_fetched=?, fetch_success=? WHERE url=?",
            (now, 1 if success else 0, url)
        )
        await db.commit()


async def upsert_article(title: str, link: str, description: str = "",
                         content: str = "", author: str = "",
                         source_name: str = "", category: str = "",
                         published_at: Optional[str] = None,
                         translated_title: str = "", translated_desc: str = ""):
    """插入或跳过重复文章。"""
    if not link or not title:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """INSERT INTO articles 
                   (title, link, description, content, author, source_name, category, published_at, source_count, translated_title, translated_desc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (title, link, description, content, author, source_name, category, published_at, translated_title, translated_desc)
            )
        except aiosqlite.IntegrityError:
            # 链接已存在 — 检查信源名是否已收录
            existing = await db.execute(
                "SELECT source_name, source_count FROM articles WHERE link = ?", (link,)
            )
            row = await existing.fetchone()
            old_sources = _split_source_names(row[0] if row else "")
            if source_name and source_name not in old_sources:
                # 新信源首次收录这篇文章，source_count+1
                old_sources.append(source_name)
            # 信源已存在则不累加 count，也不重复追加名字
            new_source = ", ".join(old_sources)
            new_count = max(1, len(old_sources))
            
            # 使用 CASE WHEN 避免空字符串覆盖已有翻译
            await db.execute(
                """UPDATE articles 
                   SET source_name = ?,
                       source_count = ?,
                       translated_title = CASE WHEN ? = '' OR translated_title IS NOT NULL AND translated_title != '' THEN translated_title ELSE ? END,
                       translated_desc = CASE WHEN ? = '' OR translated_desc IS NOT NULL AND translated_desc != '' THEN translated_desc ELSE ? END,
                       published_at = CASE WHEN ? IS NOT NULL AND ? != '' AND (published_at IS NULL OR published_at = '') THEN ? ELSE published_at END
                   WHERE link = ?""",
                (new_source,
                 new_count,
                 translated_title, translated_title,
                 translated_desc, translated_desc,
                 published_at, published_at, published_at,
                 link)
            )
        await db.commit()


async def get_articles(category: Optional[str] = None, search: Optional[str] = None,
                       limit: int = 50, offset: int = 0,
                       order_by: str = "published_at", featured_only: bool = False):
    """获取文章列表"""
    if order_by not in ("published_at", "hot_score"):
        order_by = "published_at"
    
    query = """SELECT id, title, link, description, author, source_name, category,
                      published_at, hot_score, tags, is_featured, source_count, fetched_at,
                      translated_title, translated_desc
               FROM articles WHERE 1=1"""
    params = []
    
    if category:
        query += " AND category = ?"
        params.append(category)
    if search:
        query += " AND (title LIKE ? OR translated_title LIKE ? OR description LIKE ? OR translated_desc LIKE ?)"
        search_param = f"%{search}%"
        params.extend([search_param, search_param, search_param, search_param])
    if featured_only:
        query += " AND is_featured = 1"
    
    query += f" ORDER BY {order_by} DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_articles_by_date(date_str: str):
    """按日期获取文章"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        start_date = f"{date_str} 00:00:00"
        end_date = f"{date_str} 23:59:59"
        async with db.execute(
            """SELECT id, title, link, description, author, source_name, category,
                       published_at, hot_score, tags, is_featured, source_count, fetched_at,
                       translated_title, translated_desc
               FROM articles 
               WHERE published_at BETWEEN ? AND ?
               ORDER BY published_at DESC""",
            (start_date, end_date)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_articles_by_date_range(start_date: str, end_date: str):
    """按日期范围获取文章"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, title, link, description, author, source_name, category,
                       published_at, hot_score, tags, is_featured, source_count, fetched_at,
                       translated_title, translated_desc
               FROM articles 
               WHERE published_at BETWEEN ? AND ?
               ORDER BY published_at DESC""",
            (start_date, end_date)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_categories():
    """获取所有分类"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT category FROM articles WHERE category != '' ORDER BY category"
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_sources():
    """获取所有启用的信源"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sources WHERE enabled=1") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_featured_articles(limit: int = 50):
    """获取精选文章"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, title, link, description, author, source_name, category,
                       published_at, hot_score, tags, is_featured, source_count, fetched_at,
                       translated_title, translated_desc
               FROM articles WHERE is_featured=1 ORDER BY published_at DESC LIMIT ?""",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def set_featured(article_id: int, featured: bool):
    """设置/取消精选标记"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE articles SET is_featured=? WHERE id=?",
            (1 if featured else 0, article_id)
        )
        await db.commit()


async def calculate_hot_scores():
    """
    计算所有文章的热点分数（统一算法，使用 UTC 时间）。
    算法：基础分10 + (信源数-1)*5 - 时间衰减(每小时0.5)
    使用批量更新，单次事务完成。
    """
    articles = await get_articles(limit=10000)
    if not articles:
        return

    updates = []
    for a in articles:
        pub_at = a.get("published_at", "") or ""
        src_count = a.get("source_count", 1) or 1
        score = calculate_hot_score(pub_at, src_count)
        updates.append((score, a["id"]))

    # 批量更新：单次事务完成
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "UPDATE articles SET hot_score=? WHERE id=?",
            updates
        )
        await db.commit()
    return updates


async def save_daily_digest(digest_date: str, content: str, article_count: int = 0):
    """保存日报"""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO daily_digests (digest_date, content, article_count) VALUES (?, ?, ?)",
                (digest_date, content, article_count)
            )
        except aiosqlite.IntegrityError:
            await db.execute(
                "UPDATE daily_digests SET content=?, article_count=? WHERE digest_date=?",
                (content, article_count, digest_date)
            )
        await db.commit()


async def get_daily_digest(digest_date: str):
    """按日期获取日报"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM daily_digests WHERE digest_date = ?", (digest_date,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_digest_dates():
    """获取有日报的日期列表"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT digest_date FROM daily_digests ORDER BY digest_date DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def get_article_by_id(article_id: int):
    """按 ID 获取单篇文章"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, title, link, description, author, source_name, category,
                       published_at, hot_score, tags, is_featured, source_count, fetched_at,
                       translated_title, translated_desc
               FROM articles WHERE id = ?""",
            (article_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_source_health_stats():
    """获取信源健康状态统计"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) FROM sources") as c:
            total = (await c.fetchone())[0]
        
        async with db.execute("SELECT COUNT(*) FROM sources WHERE last_fetched IS NOT NULL") as c:
            fetched = (await c.fetchone())[0]
        
        async with db.execute("SELECT COUNT(*) FROM sources WHERE fetch_success = 1") as c:
            success = (await c.fetchone())[0]
        
        async with db.execute("SELECT COUNT(*) FROM sources WHERE fetch_success = 0") as c:
            failed = (await c.fetchone())[0]
        
        # 最近失败的信源
        async with db.execute(
            "SELECT name, url, last_fetched FROM sources WHERE fetch_success = 0 ORDER BY last_fetched DESC LIMIT 5"
        ) as c:
            recent_failures = [dict(r) for r in await c.fetchall()]
        
        return {
            "total_sources": total,
            "fetched_count": fetched,
            "success_count": success,
            "failed_count": failed,
            "recent_failures": recent_failures,
        }
