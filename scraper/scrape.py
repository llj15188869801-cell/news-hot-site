"""
RSS 爬虫 - 从多个信源抓取文章，解析并存入数据库。
增强版：并发限流、重试、分批翻译、错误隔离
"""

import asyncio
import aiohttp
import feedparser
import html2text
import sys
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

_scraper_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_scraper_dir)
_backend_path = os.path.join(_project_root, "backend")
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)

from db import upsert_article, get_sources, init_db, update_source_health

# 导入翻译器
sys.path.insert(0, os.path.join(_project_root, "backend"))
from translator import translate_text_sync

# ========== 分类映射 ==========
CATEGORY_MAP = {
    "artificial-intelligence": "AI",
    "ai": "AI",
    "tech": "科技",
    "technology": "科技",
    "product": "产品",
    "products": "产品",
    "paper": "论文",
    "papers": "论文",
    "industry": "资讯",
    "general": "资讯",
    "opinion": "观点",
    "business": "商业",
    "startup": "创业",
    "science": "科学",
    "health": "健康",
    "crypto": "加密货币",
    "blockchain": "区块链",
    "machine-learning": "机器学习",
    "deep-learning": "深度学习",
    "openai": "OpenAI",
    "google": "谷歌",
    "meta": "Meta",
}


def normalize_category(raw_category: str) -> str:
    if not raw_category:
        return "资讯"
    if raw_category in CATEGORY_MAP:
        return CATEGORY_MAP[raw_category]
    raw_lower = raw_category.lower()
    for key, val in CATEGORY_MAP.items():
        if key in raw_lower:
            return val
    return raw_category[:10]


# ========== HTML 清理器 ==========
_html_cleaner = html2text.HTML2Text()
_html_cleaner.ignore_links = False
_html_cleaner.ignore_images = True


def clean_html(text: str) -> str:
    if not text:
        return ""
    return _html_cleaner.handle(text).strip()[:500]


# ========== 日期解析 ==========
def parse_published_date(item: dict) -> str:
    published = ""
    if item.get("published"):
        published = item["published"]
    elif item.get("updated"):
        published = item["updated"]
    elif item.get("dc", {}).get("date"):
        published = item["dc"]["date"]

    now_utc = datetime.now(timezone.utc)

    if not published:
        # 使用抓取时的时间作为默认值
        return now_utc.strftime("%Y-%m-%d %H:%M:%S")

    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
    ]

    cleaned = published.replace("Z", "+00:00").rstrip()

    parsed_dt = None
    for fmt in formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            parsed_dt = dt
            break
        except ValueError:
            continue

    if parsed_dt is None:
        try:
            dt = datetime.fromisoformat(cleaned)
            parsed_dt = dt
        except Exception:
            pass

    if parsed_dt is not None:
        # 统一转为 UTC naive datetime
        if parsed_dt.tzinfo is not None:
            parsed_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None)
        # 校验：如果解析时间是未来（超过当前 UTC 时间 +1 小时容差），视为异常
        now_naive = now_utc.replace(tzinfo=None)
        if parsed_dt > now_naive + timedelta(hours=1):
            parsed_dt = now_naive

    if parsed_dt is None:
        # 解析失败时使用当前时间
        parsed_dt = now_utc.replace(tzinfo=None)

    return parsed_dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_author(item: dict) -> str:
    if item.get("author"):
        return item["author"]
    if item.get("dc", {}).get("creator"):
        return item["dc"]["creator"]
    return ""


def parse_description(item: dict) -> str:
    desc = ""
    if item.get("summary"):
        desc = item["summary"]
    elif item.get("description"):
        desc = item["description"]
    elif item.get("content", {}):
        content_items = item.get("content", [])
        if content_items:
            desc = content_items[0].get("value", "")
        else:
            desc = item["content"]
    return clean_html(desc)


def parse_categories(item: dict) -> list:
    cats = item.get("categories", [])
    if not cats:
        if item.get("media", {}).get("category"):
            cats = [item["media"]["category"]]
    return cats


# ========== 翻译函数（批量版，避免 asyncio.run 并发问题） ==========
def translate_batch(translatable: list[tuple[str, str]], target_lang: str = "zh-CN") -> list[Optional[str]]:
    """
    批量翻译：接收 [(source_text, id), ...] 列表。
    全部在同步上下文中调用 translate_text_sync，避免 event loop 嵌套。
    返回与输入同序的翻译结果列表。
    """
    results = []
    for text, _id in translatable:
        if not text:
            results.append(None)
            continue
        # 如果已经有中文内容，跳过
        has_chinese = any("\u4e00" <= c <= "\u9fff" for c in text)
        if has_chinese:
            results.append(text)
            continue
        try:
            result = translate_text_sync(text, sl="en", tl=target_lang)
            results.append(result)
        except Exception as e:
            print(f"  ⚠ 翻译失败 [{_id}]: {e}")
            results.append(None)
        # 简单限流
        time.sleep(0.35)
    return results


# ========== 解析 RSS 条目（不翻译，仅解析） ==========
def parse_rss_item(item: dict, source_name: str) -> Optional[dict]:
    """解析单条 RSS 条目，返回原始数据（不含翻译）。"""
    title = item.get("title", "").strip()
    link = item.get("link", "").strip()

    if not title or not link:
        return None
    if link == "#" or not link.startswith(("http://", "https://")):
        return None

    description = parse_description(item)
    author = parse_author(item)
    source = source_name
    category = normalize_category(parse_categories(item)[0]) if parse_categories(item) else ""
    published_at = parse_published_date(item)

    return {
        "title": title,
        "link": link,
        "description": description,
        "author": author,
        "source_name": source,
        "category": category,
        "published_at": published_at,
        "raw_title": title,       # 用于后续翻译
        "raw_desc": description,  # 用于后续翻译
    }


# ========== 网络请求 ==========
async def fetch_feed(session: aiohttp.ClientSession, url: str, source_name: str = "",
                     timeout: int = 30, max_retries: int = 3) -> Optional[str]:
    """Fetch an RSS feed with retry and multiple User-Agent strategies."""
    ua_variants = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "NewsHotScraper/2.0 (compatible; bot; +https://github.com/news-hot-site)",
    ]
    
    last_error = None
    for attempt in range(1, max_retries + 1):
        ua = ua_variants[(attempt - 1) % len(ua_variants)]
        headers = {
            "User-Agent": ua,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    text = await resp.text(errors="replace")
                    ct = resp.headers.get("Content-Type", "")
                    if ct and "xml" not in ct.lower() and "rss" not in ct.lower() and "text" not in ct.lower():
                        # Not an RSS/XML response — could be HTML
                        if "<!DOCTYPE html>" in text[:500]:
                            return None  # HTML page, not RSS
                    return text
                elif resp.status == 429:
                    wait_time = min(2 ** attempt * 2, 60)
                    await asyncio.sleep(wait_time)
                elif resp.status == 403:
                    # Cloudflare challenge — try with different UA or skip
                    if attempt < max_retries:
                        await asyncio.sleep(2)
                        continue
                    return None
                elif resp.status == 405:
                    # Method not allowed — try HEAD first or skip
                    return None
                elif resp.status == 451:
                    # Unavailable for legal reasons — skip
                    return None
                elif resp.status >= 500:
                    wait_time = min(2 ** attempt * 2, 30)
                    await asyncio.sleep(wait_time)
                else:
                    return None
        except asyncio.TimeoutError:
            last_error = "超时"
            if attempt < max_retries:
                await asyncio.sleep(min(2 ** attempt * 2, 30))
        except aiohttp.ClientError as e:
            last_error = str(e)
        except Exception as e:
            last_error = str(e)

        if attempt < max_retries:
            await asyncio.sleep(min(2 ** attempt * 2, 30))

    print(f"  ✗ {source_name}: 所有 {max_retries} 次重试失败 ({last_error})")
    return None


# ========== 主抓取流程 ==========
async def scrape_all(semaphore: Optional[asyncio.Semaphore] = None):
    await init_db()

    sources = await get_sources()
    if not sources:
        print("未配置信源，注册默认信源...")
        from db import add_source
        defaults = [
            ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "资讯"),
            ("MIT Tech Review", "https://www.technologyreview.com/feed/", "资讯"),
            ("36氪 AI", "https://36kr.com/feed", "资讯"),
            ("Hacker News", "https://hnrss.org/frontpage", "产品"),
            ("钛媒体", "https://www.tmtpost.com/rss.xml", "科技"),
            ("量子位", "https://www.qbitai.com/feed", "AI"),
            ("InfoQ", "https://www.infoq.cn/feed", "科技"),
            ("爱范儿", "https://www.ifanr.com/feed", "产品"),
            ("少数派", "https://sspai.com/feed", "产品"),
        ]
        for name, url, cat in defaults:
            await add_source(name, url, cat)
        sources = await get_sources()

    if not sources:
        print("无法配置信源。")
        return

    if semaphore is None:
        semaphore = asyncio.Semaphore(5)

    print(f"找到 {len(sources)} 个 RSS 信源，开始抓取（并发限制: 5）...")

    total_new = 0
    success_count = 0
    fail_count = 0
    # 收集所有需要翻译的条目：(source_text, article_dict)
    all_translatable = []

    async with aiohttp.ClientSession() as session:
        async def limited_fetch(src):
            async with semaphore:
                return await fetch_feed(session, src["url"], source_name=src["name"], timeout=15, max_retries=2)

        tasks = [limited_fetch(src) for src in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        src = sources[i]
        if isinstance(result, Exception) or result is None:
            fail_count += 1
            print(f"  ✗ {src['name']}: 抓取失败")
            await update_source_health(src["url"], False)
            continue

        try:
            feed = feedparser.parse(result)

            if feed.bozo:
                print(f"  ⚠ {src['name']}: feedparser 警告")

            if not feed.entries:
                print(f"  ○ {src['name']}: 无内容")
                continue

            added = 0
            for entry in feed.entries:
                article = parse_rss_item(entry, src["name"])
                if article:
                    all_translatable.append((article["raw_title"], article))
                    # 暂存翻译占位
                    article["translated_title"] = ""
                    article["translated_desc"] = ""
                    await upsert_article(
                        title=article["title"],
                        link=article["link"],
                        description=article["description"],
                        content="",
                        author=article["author"],
                        source_name=article["source_name"],
                        category=article["category"],
                        published_at=article["published_at"],
                        translated_title="",
                        translated_desc="",
                    )
                    added += 1
                    total_new += 1

            success_count += 1
            print(f"  ✓ {src['name']}: {len(feed.entries)} 条, {added} 条新")
            # 更新健康状态
            await update_source_health(src["url"], True)

        except Exception as e:
            fail_count += 1
            print(f"  ✗ {src['name']}: 解析失败 — {e}")
            await update_source_health(src["url"], False)

    # ========== 批量翻译阶段（在主线程中顺序执行，避免 asyncio.run 嵌套） ==========
    if all_translatable:
        # 预检：从数据库中查询这些文章是否已有翻译，跳过已翻译的
        import sqlite3
        db_path = os.path.join(_project_root, "data", "news.db")
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            links_to_check = [a["link"] for _, a in all_translatable]
            placeholders = ",".join("?" for _ in links_to_check)
            cursor = conn.execute(
                f"SELECT link, translated_title, translated_desc FROM articles WHERE link IN ({placeholders})",
                links_to_check
            )
            existing_translations = {}
            for row in cursor:
                if row["translated_title"]:
                    existing_translations[row["link"]] = dict(row)
            conn.close()
        except Exception as e:
            print(f"  ⚠ 预检翻译失败: {e}")
            existing_translations = {}

        # 过滤掉已有翻译的文章
        skip_count = 0
        filtered = []
        for idx, (text, article) in enumerate(all_translatable):
            if article["link"] in existing_translations:
                skip_count += 1
                # 直接从数据库回填翻译占位
                article["translated_title"] = existing_translations[article["link"]].get("translated_title", "")
                article["translated_desc"] = existing_translations[article["link"]].get("translated_desc", "")
            else:
                filtered.append((idx, text, article))
        
        if skip_count:
            print(f"  ○ 跳过 {skip_count} 条已翻译文章")
        
        if filtered:
            print(f"\n  开始批量翻译 {len(filtered)} 条标题...")
            titles_to_translate = [(text, idx) for idx, (orig_idx, text, _) in enumerate(filtered)]
            title_results = translate_batch(titles_to_translate, "zh-CN")

            # 同时翻译描述
            descs_to_translate = [(article["raw_desc"], idx) for idx, (orig_idx, text, article) in enumerate(filtered)
                                  if article.get("raw_desc") and not any("\u4e00" <= c <= "\u9fff" for c in article["raw_desc"])]
            desc_results = translate_batch(descs_to_translate, "zh-CN") if descs_to_translate else []

            # 用 sqlite3 直接批量更新（在 asyncio context 中调 asyncio.run 有 bug）
            if title_results:
                updates = []
                skipped = 0
                for idx, result in enumerate(title_results):
                    if result:
                        orig_idx, article = filtered[idx][0], filtered[idx][2]
                        updates.append((result, article["link"]))
                    else:
                        skipped += 1

                # 构建翻译描述的映射
                desc_updates = []
                if desc_results and descs_to_translate:
                    for desc_idx, result in enumerate(desc_results):
                        if result:
                            all_idx = descs_to_translate[desc_idx][1]
                            article = filtered[all_idx][2]
                            desc_updates.append((result, article["link"]))

                if updates or desc_updates:
                    try:
                        conn = sqlite3.connect(db_path)
                        conn.execute("BEGIN IMMEDIATE")
                        for translated_title, link in updates:
                            conn.execute(
                                "UPDATE articles SET translated_title=? WHERE link=?",
                                (translated_title, link)
                            )
                        for translated_desc, link in desc_updates:
                            conn.execute(
                                "UPDATE articles SET translated_desc=? WHERE link=?",
                                (translated_desc, link)
                            )
                        conn.commit()
                        conn.close()
                        print(f"  批量翻译完成: {len(updates)} 条标题已更新, {len(desc_updates)} 条描述已更新, {skipped} 条跳过")
                    except Exception as e:
                        print(f"  ⚠ 批量更新翻译 DB 失败: {e}")
                        try:
                            conn.close()
                        except Exception:
                            pass
        else:
            print("  ○ 无需翻译（全部已翻译）")

    print(f"\n{'='*50}")
    print(f"抓取完成：成功 {success_count}/{len(sources)} 个信源")
    print(f"共获取 {total_new} 篇文章")
    if fail_count > 0:
        print(f"失败 {fail_count} 个信源")

    # 抓取完成后自动计算热点分数
    from db import calculate_hot_scores, get_articles, set_featured, get_featured_articles
    try:
        updates = await calculate_hot_scores()
        if updates:
            scored = sum(1 for u in updates if u[0] > 0)
            print(f"热点分数已重新计算: {len(updates)} 篇文章, {scored} 篇有分数")
        else:
            print("热点分数计算: 无文章需要更新")
    except Exception as e:
        print(f"⚠ 热点分数计算失败: {e}")

    # 自动精选：选取热点分数最高的前 15 篇标记为精选
    FEATURED_COUNT = 15
    try:
        # 先取消旧的精选
        old_featured = await get_featured_articles(limit=1000)
        for fa in old_featured:
            await set_featured(fa["id"], False)
        # 按热点分数获取文章
        articles = await get_articles(limit=200, order_by="hot_score")
        for i, art in enumerate(articles[:FEATURED_COUNT]):
            await set_featured(art["id"], True)
        print(f"自动精选: {min(len(articles), FEATURED_COUNT)} 篇文章已标记")
    except Exception as e:
        print(f"⚠ 自动精选失败: {e}")


if __name__ == "__main__":
    start_time = time.time()
    asyncio.run(scrape_all())
    elapsed = time.time() - start_time
    print(f"总耗时: {elapsed:.1f}s")
