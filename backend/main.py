"""
新闻热点 - FastAPI 应用，HTML 模板 + JSON API。
类似 AIHOT：首页时间线 + 热点板块 + 精选页面 + 日报。
"""

import os
import sys
import re
import asyncio
from datetime import datetime, timedelta, date
from typing import Optional
import math
from functools import lru_cache

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from db import (
    init_db, add_source, upsert_article, get_articles, get_articles_by_date,
    get_categories, get_sources, save_daily_digest,
    get_daily_digest, get_digest_dates,
    get_articles_by_date_range, get_featured_articles,
    set_featured, calculate_hot_scores, get_article_by_id
)

app = FastAPI(title="新闻热点", version="2.0.0")

# ========== 搜索输入长度限制 ==========
MAX_SEARCH_LENGTH = 200


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动和关闭时执行"""
    # Startup
    await init_db()
    for name, url, cat in DEFAULT_SOURCES:
        await add_source(name, url, cat)
    await calculate_hot_scores()
    yield
    # Shutdown


app.router.lifespan_context = lifespan

# 模板目录
_templates_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
_templates_dir = os.path.abspath(_templates_dir)
templates = Jinja2Templates(directory=_templates_dir)

# 默认 RSS 信源
DEFAULT_SOURCES = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "资讯"),
    ("36氪 AI", "https://36kr.com/feed", "资讯"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/", "资讯"),
    ("Hacker News", "https://hnrss.org/frontpage", "产品"),
]


# ========== 热点算法 ==========

def calculate_hot_score_for_article(published_at_str: str, source_count: int) -> float:
    base = 10.0
    source_bonus = (source_count - 1) * 5.0
    now = datetime.now()
    try:
        pub_str = published_at_str or ""
        if pub_str:
            pub_time = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        else:
            pub_time = now
    except (TypeError, ValueError, AttributeError):
        pub_time = now

    try:
        if pub_time.tzinfo is not None:
            pub_time_utc = pub_time.utctimetuple()
            now_utc = datetime.utcnow()
            hours_ago = max(0, (now_utc - datetime(*pub_time_utc[:6])).total_seconds() / 3600)
        else:
            hours_ago = max(0, (now - pub_time).total_seconds() / 3600)
    except (TypeError, ValueError):
        hours_ago = 0

    decay = hours_ago * 0.5
    return max(0.0, base + source_bonus - decay)


@lru_cache(maxsize=512)
def _hot_score_cached(published_at: str, source_count: int) -> float:
    return calculate_hot_score_for_article(published_at, source_count)


def _hot_score(article: dict) -> float:
    pub_at = article.get("published_at", "") or ""
    src_count = article.get("source_count", 1) or 1
    try:
        return _hot_score_cached(pub_at, src_count)
    except Exception:
        return 10.0


# ========== 全局错误处理 ==========

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "输入验证失败", "errors": str(exc)}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    print(f"[ERROR] Unhandled exception on {request.method} {request.url.path}: {exc}", file=sys.stderr)
    print(traceback.format_exc(), file=sys.stderr)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误"}
    )


# ========== 健康检查 ==========

@app.get("/health")
async def health_check():
    import aiosqlite
    from db import DB_PATH, get_source_health_stats
    stats = {}
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM articles") as c:
                stats["total_articles"] = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM sources") as c:
                stats["total_sources"] = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM articles WHERE translated_title IS NOT NULL AND translated_title != ''") as c:
                stats["translated_articles"] = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM articles WHERE is_featured = 1") as c:
                stats["featured_articles"] = (await c.fetchone())[0]
            async with db.execute("SELECT MIN(published_at), MAX(published_at) FROM articles") as c:
                row = await c.fetchone()
                stats["date_range_start"] = row[0] if row and row[0] else None
                stats["date_range_end"] = row[1] if row and row[1] else None
    except Exception as e:
        stats["db_error"] = str(e)
    
    # 信源健康明细
    try:
        source_stats = await get_source_health_stats()
        stats["source_health"] = source_stats
    except Exception as e:
        stats["source_health_error"] = str(e)
    
    return JSONResponse({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "database": stats,
    })


# ========== 页面路由 ==========

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, category: Optional[str] = Query(None)):
    """首页：热点板块 + 时间线文章"""
    articles = await get_articles(category=category, limit=100)
    categories = await get_categories()
    digest_dates = await get_digest_dates()

    scored = [(_hot_score(a), a) for a in articles]
    scored.sort(key=lambda x: x[0], reverse=True)
    hot_articles = [a for _, a in scored[:10]]

    return templates.TemplateResponse("index.html", {
        "request": request,
        "articles": articles,
        "hot_articles": hot_articles,
        "categories": categories,
        "selected_category": category,
        "search_query": None,
        "digest_dates": digest_dates,
    })


@app.get("/curated")
async def curated(request: Request):
    """精选页面"""
    featured = await get_featured_articles(limit=50)
    categories = await get_categories()
    digest_dates = await get_digest_dates()
    return templates.TemplateResponse("curated.html", {
        "request": request,
        "articles": featured,
        "categories": categories,
        "digest_dates": digest_dates,
    })


@app.get("/article/{article_id}")
async def article_detail(request: Request, article_id: int):
    """文章详情页"""
    article = await get_article_by_id(article_id)
    if not article:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    return templates.TemplateResponse("article.html", {
        "request": request,
        "article": article,
    })


@app.get("/search")
async def search(request: Request, q: str = Query(..., min_length=1, max_length=MAX_SEARCH_LENGTH)):
    """搜索页面"""
    q = re.sub(r'[^\w\s\-_,.!?,，。！？、；：""''（）【】《》]', '', q)[:MAX_SEARCH_LENGTH]
    articles = await get_articles(search=q, limit=100)
    categories = await get_categories()
    digest_dates = await get_digest_dates()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "articles": articles,
        "hot_articles": [],
        "categories": categories,
        "selected_category": None,
        "search_query": q,
        "digest_dates": digest_dates,
    })


@app.get("/daily/{date_str}")
async def daily_page(request: Request, date_str: str):
    """日报页面"""
    try:
        date.fromisoformat(date_str)
    except ValueError:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    articles = await get_articles_by_date(date_str)
    digest = await get_daily_digest(date_str)
    digest_dates = await get_digest_dates()
    return templates.TemplateResponse("daily.html", {
        "request": request,
        "articles": articles,
        "digest": digest,
        "date_str": date_str,
        "digest_dates": digest_dates,
    })


# ========== API ==========

@app.get("/api/articles")
async def api_articles(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order_by: str = Query("published_at", regex="^(published_at|hot_score)$"),
):
    """文章列表 API，支持按 hot_score 排序"""
    articles = await get_articles(category=category, search=search, limit=limit, offset=offset, order_by=order_by)
    return JSONResponse({"articles": articles, "count": len(articles)})


@app.get("/api/hot")
async def api_hot(limit: int = Query(10, ge=1, le=100)):
    """热点 API：按热度排序"""
    articles = await get_articles(limit=200)
    scored = [(_hot_score(a), a) for a in articles]
    scored.sort(key=lambda x: x[0], reverse=True)
    return JSONResponse({"articles": [a for _, a in scored[:limit]]})


@app.get("/api/featured")
async def api_featured(limit: int = Query(20, ge=1, le=100)):
    """精选 API"""
    articles = await get_featured_articles(limit=limit)
    return JSONResponse({"articles": articles})


@app.get("/api/categories")
async def api_categories():
    """分类 API"""
    cats = await get_categories()
    return JSONResponse({"categories": cats})


@app.get("/api/sources")
async def api_sources():
    """信源 API"""
    sources = await get_sources()
    return JSONResponse({"sources": sources})


@app.get("/api/daily/{date_str}")
async def api_daily(date_str: str):
    """日报 API"""
    try:
        date.fromisoformat(date_str)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"error": "无效的日期格式，使用 YYYY-MM-DD"}
        )
    articles = await get_articles_by_date(date_str)
    digest = await get_daily_digest(date_str)
    return JSONResponse({
        "date": date_str,
        "articles": articles,
        "digest": digest,
    })


# ========== 翻译 API ==========

@app.get("/api/translate/{article_id}")
async def api_translate_article(article_id: int):
    """翻译指定文章的标题和描述为中文"""
    from translator import translate_text_sync
    
    article = await get_article_by_id(article_id)
    if not article:
        return JSONResponse(status_code=404, content={"error": "文章不存在"})
    
    title = article.get("title", "")
    desc = article.get("description", "")
    
    translated_title = translate_text_sync(title)
    translated_desc = translate_text_sync(desc) if desc else ""
    
    # 保存到数据库
    await upsert_article(
        title=title,
        link=article.get("link", ""),
        description=article.get("description", ""),
        translated_title=translated_title or "",
        translated_desc=translated_desc or "",
    )
    
    return JSONResponse({
        "article_id": article_id,
        "original_title": title,
        "translated_title": translated_title,
        "original_desc": desc,
        "translated_desc": translated_desc,
    })


@app.post("/api/translate/all")
async def api_translate_all():
    """批量翻译所有未翻译的文章"""
    from translator import translate_text_sync
    import aiosqlite
    from db import DB_PATH
    
    articles = await get_articles(limit=200)
    translated_count = 0
    
    async with aiosqlite.connect(DB_PATH) as db:
        for article in articles:
            tt = article.get("translated_title")
            td = article.get("translated_desc")
            if tt and td:
                continue  # 已有翻译，跳过
            
            title = article.get("title", "")
            desc = article.get("description", "") or ""
            
            new_tt = translate_text_sync(title) if not tt else tt
            new_td = translate_text_sync(desc) if not td and desc else td
            
            if new_tt or new_td:
                link = article.get("link", "")
                await db.execute(
                    "UPDATE articles SET translated_title = ?, translated_desc = ? WHERE link = ?",
                    (new_tt or "", new_td or "", link)
                )
                await db.commit()
                translated_count += 1
            
            await asyncio.sleep(0.3)  # 频率限制
    
    return JSONResponse({
        "translated_count": translated_count,
        "total_articles": len(articles),
    })


# ========== API 速率限制 ==========
from collections import defaultdict
import time as _time

_request_counts: dict = defaultdict(list)
RATE_LIMIT_REQUESTS = 60  # 每分钟最大请求数
RATE_LIMIT_WINDOW = 60    # 窗口秒数

@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """简单基于 IP 的 API 速率限制中间件"""
    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()
    
    # 清理过期记录
    _request_counts[client_ip] = [t for t in _request_counts[client_ip] if now - t < RATE_LIMIT_WINDOW]
    
    # 检查是否超限
    if len(_request_counts[client_ip]) >= RATE_LIMIT_REQUESTS:
        return JSONResponse(
            status_code=429,
            content={"error": "请求过于频繁，请稍后再试", "retry_after": RATE_LIMIT_WINDOW}
        )
    
    _request_counts[client_ip].append(now)
    response = await call_next(request)
    return response

# 静态文件
static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
