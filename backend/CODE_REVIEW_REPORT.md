# 后端代码审查报告：news_hot_site

**审查日期**: 2026-06-15
**审查文件**: `backend/main.py` (218 行), `backend/db.py` (254 行), `scraper/scrape.py` (198 行)
**审查人**: Hermes Agent (后端专家)

---

## 概要

共发现 **23 个问题**，其中：
- 🔴 **严重 (Critical)**: 4 个 — 数据一致性、逻辑错误
- 🟠 **高 (High)**: 5 个 — 性能瓶颈、API 设计缺陷
- 🟡 **中 (Medium)**: 9 个 — 可维护性、健壮性
- 🟢 **低 (Low)**: 5 个 — 风格、建议性改进

---

## 1. 🔴 严重问题 (Critical)

### CR-01 | 热点算法严重不一致 | main.py:72 vs db.py:212

**问题描述**:
`_hot_score()`（运行时计算，用于 API 和页面展示）与 `calculate_hot_scores()`（持久化计算，写入 DB）使用了 **完全不同的算法公式**：

- `_hot_score()` (main.py:72):
  ```python
  base = 10.0
  source_bonus = article.get("hot_score", 0)   # 直接读 hot_score 字段（递归依赖！）
  decay = max(0, hours_ago) * 0.5
  return base + source_bonus - decay
  ```

- `calculate_hot_scores()` (db.py:212):
  ```python
  score = 10.0 + (a.get("source_count", 1) - 1) * 5.0 - hours_ago * 0.5
  ```

**影响分析**:
1. **递归依赖 Bug**: `_hot_score()` 用 `hot_score` 字段自身作为输入，但该字段正是 `calculate_hot_scores()` 写入的。如果 `calculate_hot_scores()` 没有正确运行（或运行后有新文章插入），`_hot_score()` 读到的是旧值/零值，导致分数计算 **完全错误**。
2. **两个算法结果不同**: 前者用 `hot_score` 字段（可能是零），后者用 `(source_count-1)*5`。同一篇文章在 DB 中存一个分数，运行时用另一个逻辑计算，数据永远对不上。
3. **时间计算差异**: `calculate_hot_scores()` 用 `datetime.now()`（本地时区），`_hot_score()` 用 `datetime.now()`（本地时区），但两者 `fromisoformat` 处理时可能产生时区偏移（一个带 tzinfo 一个不带），导致 `total_seconds()` 偏差。

**修复建议**:
```python
# 统一为一个权威算法函数
def calculate_hot_score(article: dict, now: datetime = None) -> float:
    if now is None:
        now = datetime.now()
    base = 10.0
    source_count = article.get("source_count", 1)
    source_bonus = (source_count - 1) * 5.0
    
    try:
        pub_time = datetime.fromisoformat(
            article["published_at"].replace("Z", "+00:00")
        )
        # 统一为 naive datetime 比较
        if pub_time.tzinfo is not None:
            pub_time = pub_time.replace(tzinfo=None)
    except Exception:
        pub_time = now
    
    hours_ago = max(0, (now - pub_time).total_seconds()) / 3600
    decay = hours_ago * 0.5
    return base + source_bonus - decay
```
- `main.py` 的 `_hot_score()` 替换为调用 `calculate_hot_score()`
- `db.py` 的 `calculate_hot_scores()` 也用统一函数
- `hot_score` 字段改为可选的 **缓存字段**，而非权威值

---

### CR-02 | 热点分数递归依赖导致分数衰减循环 | main.py:72

**问题描述**:
`_hot_score()` 中 `source_bonus = article.get("hot_score", 0)` — 将 **已经包含时间衰减的持久化分数** 再次作为 `base + source_bonus - decay` 的输入。这意味着：
- 第一次运行 `calculate_hot_scores()`: score = 10 + bonus - decay → 存入 DB
- 下次请求 `_hot_score()`: 读到 DB 的 score（已是 decayed 值）→ 再减去一次 decay → 分数被 **双重衰减**
- 随着时间推移，分数会越来越低，且没有上限

**影响分析**: 热点排名完全失真，老文章的分数会以 2× 速度衰减。

**修复建议**: 如 CR-01 所述，统一算法，`_hot_score()` 应直接使用 `source_count` 计算，而非读取 `hot_score` 字段。`hot_score` 仅作为优化缓存。

---

### CR-03 | 每次请求打开/关闭数据库连接 — 严重性能问题 | db.py 全部函数

**问题描述**:
每个 `db.py` 函数都 `async with aiosqlite.connect(DB_PATH) as db:` 打开新连接并立即关闭。SQLite 在文件锁机制下不支持高并发写入，每次调用都是独立的连接生命周期。

**影响分析**:
- `calculate_hot_scores()` 循环中每个文章都 `connect → execute → commit → disconnect`（db.py:213-218）— 如果 1000 篇文章，就是 **2000 次连接操作 + 1000 次事务提交**。
- SQLite 在 Windows 上连接创建开销更高。
- `upsert_article()` 在 scraper 循环中也逐个 connect/commit，每篇文章一次。
- 无 WAL 模式，无连接池，无批量操作。

**修复建议**:
1. 启用 WAL 模式: `PRAGMA journal_mode=WAL`
2. 使用全局连接池或单例连接
3. `calculate_hot_scores()` 改为单事务批量更新
4. `upsert_article()` 改为批量 upsert（见下方 CR-04）

---

### CR-04 | `upsert_article` 缺少源名追加 | db.py:82-101

**问题描述**:
当同一 `link` 已存在时（`except` 分支, db.py:97-100），只 `source_count + 1`，但 **不更新 `source_name`**。如果文章来自第一个信源"TechCrunch"，又出现在"机器之心"，`source_name` 字段只保留"TechCrunch"，丢失"机器之心"的线索。

**影响分析**: 无法追溯文章来源的完整信息，`source_name` 字段不准确。

**修复建议**:
```python
except Exception:
    await db.execute(
        "UPDATE articles SET source_count = source_count + 1, "
        "source_name = source_name || ' / ?' WHERE link = ?",
        (source_name, link)
    )
```

---

## 2. 🟠 高优先级问题 (High)

### H-01 | `calculate_hot_scores()` 逐条 UPDATE+COMMIT — 性能灾难 | db.py:206-218

**问题描述**:
```python
for a in articles:
    # ... 计算分数 ...
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE articles SET hot_score=? WHERE id=?", (score, a["id"]))
        await db.commit()  # 每条记录都提交！
```

**影响分析**: 1000 篇文章 = 1000 次独立事务。每次事务涉及磁盘同步。估计耗时 10-30 秒。

**修复建议**:
```python
async def calculate_hot_scores():
    articles = await get_articles(limit=1000)
    now = datetime.now()
    updates = []
    for a in articles:
        try:
            pub_time = datetime.fromisoformat(a["published_at"].replace("Z", "+00:00"))
            if pub_time.tzinfo:
                pub_time = pub_time.replace(tzinfo=None)
        except Exception:
            pub_time = now
        hours_ago = max(0, (now - pub_time).total_seconds()) / 3600
        score = 10.0 + (a.get("source_count", 1) - 1) * 5.0 - hours_ago * 0.5
        updates.append((score, a["id"]))
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        async with db.cursor() as cursor:
            for score, article_id in updates:
                await cursor.execute(
                    "UPDATE articles SET hot_score=? WHERE id=?",
                    (score, article_id)
                )
        await db.commit()
```

---

### H-02 | 无排序 API — 热点排序全部内存排序 | main.py:90-93, 179-182

**问题描述**:
`get_articles()` 的 SQL 硬编码 `ORDER BY published_at DESC`（db.py:121），不支持 `ORDER BY hot_score`。`_hot_score()` 计算后在 Python 中 `scored.sort()`（main.py:92）。

**影响分析**: 
- `limit=100` 时 100 条 Python 对象排序 — 可接受
- `api_hot()` 用 `limit=200`（main.py:179）— 拉到 200 条再排序
- 随着文章增长（1000+ 条），内存排序效率下降，且无法利用 DB 索引
- 虽然 `idx_articles_hot` 索引存在，但从未被利用

**修复建议**: 
- `get_articles()` 增加 `order_by` 参数
- `api_hot()` 改用 DB 查询 `ORDER BY hot_score DESC LIMIT ?`，只取 top N
- 或者直接用 `calculate_hot_scores()` 预计算结果

---

### H-03 | `get_articles` 缺少 `order_by` 参数 | db.py:104-125

**问题描述**:
`get_articles()` 的 SQL 固定为 `ORDER BY published_at DESC`，无参数化排序选项。

**修复建议**:
```python
async def get_articles(category=None, search=None, limit=50, offset=0, 
                       order_by="published_at"):
    # ... 验证 order_by 防止 SQL 注入 ...
    allowed = {"published_at", "hot_score", "source_count", "fetched_at"}
    if order_by not in allowed:
        order_by = "published_at"
    query += f" ORDER BY {order_by} DESC LIMIT ? OFFSET ?"
```

---

### H-04 | 搜索不区分大小写 | main.py:135, db.py:118-120

**问题描述**:
```python
# db.py:120
params.extend([f"%{search}%", f"%{search}%"])
```
SQLite 的 `LIKE` 在 Windows 上默认区分大小写（取决于编译选项），中文不受影响但英文搜索可能失效。

**影响分析**: 用户搜索 "AI" 可能搜不到标题含 "ai" 的文章（虽然 SQLite 默认 LIKE 对 ASCII 不区分大小写，但行为依赖于编译选项）。

**修复建议**:
```python
# 使用 LOWER() 确保跨平台一致
query += " AND (LOWER(title) LIKE ? OR LOWER(description) LIKE ?)"
params.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
```

---

### H-05 | 无健康检查端点 | main.py (缺失)

**问题描述**:
无 `/health` 或 `/ping` 端点。Docker/K8s 等编排系统无法检测服务可用性。

**影响分析**: 运维监控盲区，负载均衡器无法检测服务状态。

**修复建议**:
```python
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
```

---

## 3. 🟡 中等问题 (Medium)

### M-01 | lifespan 中 init_db 可能阻塞事件循环 | main.py:36

**问题描述**:
`init_db()` 虽然是 async，但 SQLite 的 `CREATE TABLE` 和 `COMMIT` 在 Windows 上可能是阻塞式 I/O。aiosqlite 内部用线程池执行同步 sqlite3 调用，大量 SQL 语句会排队。

**影响分析**: 启动时间可能比预期长，但通常只执行一次，影响有限。

---

### M-02 | scraper 中 upsert_article 逐条 connect/commit | scrape.py:188

**问题描述**:
```python
for entry in feed.entries:
    article = parse_rss_item(entry, src["name"])
    if article:
        await upsert_article(**article)  # 每个文章一个连接+事务
```

**影响分析**: 每个 RSS feed 可能有 30-50 篇文章，每篇一次 connect + commit。如果 6 个信源，就是 180-300 次连接操作。

**修复建议**: 提供批量 `upsert_articles(articles: list)` 函数，单次事务完成所有插入。

---

### M-03 | 缺少请求日志/中间件 | main.py (缺失)

**问题描述**:
无请求日志记录。无法追踪哪些 API 被调用、响应时间、错误率。

**影响分析**: 生产环境无观测性。

**修复建议**:
```python
from fastapi import Request
import time

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    print(f"{request.method} {request.url.path} {response.status_code} {duration:.3f}s")
    return response
```

---

### M-04 | `add_source` 异常处理吞掉真实错误 | db.py:66-79

**问题描述**:
```python
try:
    await db.execute("INSERT INTO sources ...")
except Exception:  # 吞掉所有异常： constraint, connection, syntax...
    await db.execute("UPDATE sources ...")
```

**影响分析**: 如果 `INSERT` 失败是因为连接断开或语法错误，`UPDATE` 也会失败，但异常被静默吞掉。如果 `INSERT` 失败但源确实不存在，`UPDATE` 静默无操作（无异常，也没修改任何行）。

**修复建议**:
```python
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
```

---

### M-05 | `add_source` UPDATE 后无行数检查 | db.py:75-78

**问题描述**:
如果 `INSERT` 失败（非 IntegrityError），`UPDATE` 执行但可能影响 0 行。无反馈。

**修复建议**: 检查 `cursor.rowcount`。

---

### M-06 | `get_daily_digest` 返回 `None` 未处理 | main.py:153, 207

**问题描述**:
`get_daily_digest()` 在 db.py:239-244 可能返回 `None`，但 `main.py:153` 直接传入模板。如果模板未处理 `None`，Jinja2 渲染会出错或显示 `None`。

**修复建议**: 模板中检查 `if digest`，或返回空字典。

---

### M-07 | 时区处理不一致 | main.py:75, db.py:208

**问题描述**:
`_hot_score()` 和 `calculate_hot_scores()` 都用 `datetime.now()`（naive，本地时区）减去 `fromisoformat(...).replace("Z", "+00:00")`（aware，UTC）。naive datetime 与 aware datetime 相减在 Python 3.6+ 会抛 `TypeError`。

**影响分析**: **运行时抛出 TypeError**，热点分数计算完全失败，返回 `datetime.now()` 的兜底值，所有文章分数一样。

**修复建议**: 统一处理时区：
```python
pub_time = datetime.fromisoformat(...)
if pub_time.tzinfo is not None:
    pub_time = pub_time.replace(tzinfo=None)  # 转 naive
```

---

### M-08 | `DEFAULT_SOURCES` 硬编码在 main.py 中 | main.py:52-59

**问题描述**:
默认信源列表同时出现在 `main.py`（app 启动）和 `scrape.py`（爬虫初始化）。如果修改信源，需要同步两处。

**影响分析**: 维护负担，容易不一致。

**修复建议**: 提取到独立配置文件（如 `config.py` 或 `.env`）。

---

### M-09 | `article_detail` 加载 limit=1000 再内存查找 | main.py:122-123

**问题描述**:
```python
articles = await get_articles(limit=1000)
article = next((a for a in articles if a["id"] == article_id), None)
```
为获取单篇文章，拉取 1000 条并在内存中查找。

**影响分析**: 浪费带宽和内存。如果文章 ID 超出前 1000 条，返回 404 错误。

**修复建议**: 添加 `get_article_by_id()` 函数：
```python
# db.py
async def get_article_by_id(article_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM articles WHERE id=?", (article_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
```

---

## 4. 🟢 低优先级问题 (Low)

### L-01 | `sys.path.insert` 全局修改 | main.py:20

**问题描述**:
`sys.path.insert(0, ...)` 在模块导入时修改全局 sys.path，如果项目被其他代码 import 会产生副作用。

**修复建议**: 使用相对导入或将 `backend` 打包为包。

---

### L-02 | 缺少 CORS 中间件 | main.py (缺失)

**问题描述**:
JSON API 端点无 CORS 配置。前端（如有）在不同域名请求会被浏览器拦截。

**修复建议**:
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境指定域名
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

### L-03 | `limit` / `offset` 无上限校验 | main.py:169-170, 177

**问题描述**:
`limit: int = 50` 无最大值限制。恶意请求 `?limit=1000000` 可导致大量内存分配。

**修复建议**:
```python
limit: int = Query(50, ge=1, le=200)
```

---

### L-04 | 缺少 Rate Limiting | main.py (缺失)

**问题描述**:
RSS 聚合端点（`/api/articles`, `/api/hot`）无速率限制，可被滥用。

**修复建议**: 使用 `slowapi` 或自定义中间件。

---

### L-05 | `404.html` 通过 TemplateResponse 返回但 status_code 用法可疑 | main.py:125

**问题描述**:
```python
return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
```
FastAPI 的 `TemplateResponse` 构造函数 **不支持 `status_code` 关键字参数**（它继承自 `Response`，但 Jinja2Templates.TemplateResponse 不接受此参数）。这会导致运行时 TypeError。

**修复建议**:
```python
response = templates.TemplateResponse("404.html", {"request": request})
response.status_code = 404
return response
```

---

### L-06 | scraper 中 `parse_rss_item` 的 `published_at` 可能为空字符串 | scrape.py:95-111

**问题描述**:
如果所有日期格式都解析失败，`published` 保持 `""` 空字符串，传入 `published_at` 字段。`DATE(published_at) = ?` 查询时可能行为异常。

**修复建议**: 解析失败时设为 `NULL` 或默认值。

---

### L-07 | `CATEGORY_MAP` 硬编码且无扩展性 | scrape.py:24-37

**问题描述**:
分类映射硬编码，无外部配置。新增 RSS 信源分类需要改代码。

**修复建议**: 移至配置或数据库。

---

## 完整问题汇总表

| 编号 | 严重程度 | 文件 | 行号 | 问题 |
|------|---------|------|------|------|
| CR-01 | 🔴 严重 | main.py:72, db.py:212 | 热点算法不一致 |
| CR-02 | 🔴 严重 | main.py:72 | 热点分数递归依赖 |
| CR-03 | 🔴 严重 | db.py (全部) | 每次请求打开/关闭 DB 连接 |
| CR-04 | 🔴 严重 | db.py:82-101 | upsert 不追加源名 |
| H-01 | 🟠 高 | db.py:206-218 | 逐条 UPDATE+COMMIT |
| H-02 | 🟠 高 | main.py:90-93, 179-182 | 热点排序全部内存计算 |
| H-03 | 🟠 高 | db.py:104-125 | get_articles 无 order_by 参数 |
| H-04 | 🟠 高 | db.py:118-120 | 搜索不统一大小写 |
| H-05 | 🟠 高 | main.py (缺失) | 无健康检查端点 |
| M-01 | 🟡 中 | main.py:36 | lifespan 启动可能阻塞 |
| M-02 | 🟡 中 | scrape.py:188 | scraper 逐条 connect/commit |
| M-03 | 🟡 中 | main.py (缺失) | 无请求日志 |
| M-04 | 🟡 中 | db.py:66-79 | add_source 异常处理吞错 |
| M-05 | 🟡 中 | db.py:75-78 | add_source UPDATE 无行数检查 |
| M-06 | 🟡 中 | main.py:153 | get_daily_digest 返回 None 未处理 |
| M-07 | 🟡 中 | main.py:75, db.py:208 | 时区处理不一致（naive/aware 混合） |
| M-08 | 🟡 中 | main.py:52-59 | DEFAULT_SOURCES 重复定义 |
| M-09 | 🟡 中 | main.py:122-123 | article_detail 加载 limit=1000 |
| L-01 | 🟢 低 | main.py:20 | sys.path.insert 副作用 |
| L-02 | 🟢 低 | main.py (缺失) | 缺少 CORS 中间件 |
| L-03 | 🟢 低 | main.py:169-170 | limit/offset 无上限校验 |
| L-04 | 🟢 低 | main.py (缺失) | 缺少 Rate Limiting |
| L-05 | 🟢 低 | main.py:125 | TemplateResponse status_code 用法 |
| L-06 | 🟢 低 | scrape.py:95-111 | published_at 可能为空字符串 |
| L-07 | 🟢 低 | scrape.py:24-37 | CATEGORY_MAP 硬编码 |

---

## 优先级修复建议

**P0（立即修复）**: CR-01, CR-02, CR-03, CR-04 — 修复核心算法一致性和数据正确性
**P1（本周修复）**: H-01, H-02, H-03, H-04, H-05 — 性能优化和 API 完善
**P2（下周修复）**: M-01 至 M-09 — 健壮性和可维护性
**P3（持续改进）**: L-01 至 L-07 — 最佳实践
