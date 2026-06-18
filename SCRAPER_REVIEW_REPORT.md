# RSS Scraper 健壮性与可靠性审查报告

**审查文件**: `scraper/scrape.py` (198 行)  
**审查日期**: 2026-06-15  
**审查范围**: 并发控制、错误处理、RSS 解析、增量抓取、信源健康、数据完整性  
**关联文件**: `backend/db.py` (upsert 逻辑)

---

## 总览

| 严重程度 | 数量 | 编号 |
|----------|------|------|
| 🔴 严重 | 4 | S-01, S-02, S-03, S-04 |
| 🟠 高 | 5 | H-01 ~ H-05 |
| 🟡 中 | 6 | M-01 ~ M-06 |
| ℹ️ 低/改进 | 5 | L-01 ~ L-05 |

---

## 🔴 严重问题 (Critical)

### S-01: 无并发限流 — 批量触发 RSS 源反爬

**位置**: `scrape.py` 第 165-167 行

```python
async with aiohttp.ClientSession() as session:
    tasks = [fetch_feed(session, src["url"], source_name=src["name"]) for src in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)
```

**问题**: `asyncio.gather` 无限制地并发执行所有信源的 HTTP 请求。如果信源数量较多（例如 50+），将同时发起 50+ 个 TCP 连接。许多 RSS 源（尤其是国内源如机器之心）会触发以下反爬措施：
- 返回 429 Too Many Requests
- 连接被拒绝/重置
- IP 被封禁

**影响**: 大规模抓取时成功率急剧下降；部分信源可能被误封。

**修复建议**:
```python
# 使用 Semaphore 限制并发数
semaphore = asyncio.Semaphore(5)  # 最多 5 个并发

async def fetch_with_limit(session, url, source_name):
    async with semaphore:
        return await fetch_feed(session, url, source_name)

tasks = [fetch_with_limit(session, src["url"], source_name=src["name"]) for src in sources]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

---

### S-02: 无指数退避重试 — 临时网络故障即丢数据

**位置**: `scrape.py` 第 124-138 行 (`fetch_feed`)

```python
try:
    async with session.get(url, headers=headers, timeout=timeout) as resp:
        ...
except Exception as e:
    print(f"  ✗ {url}: {e}")
    return None
```

**问题**: 
- 任何异常（DNS 失败、连接超时、SSL 错误等）都直接返回 `None`，**零重试**
- HTTP 5xx 错误码（502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout）被当作正常响应处理（只打印警告，返回 `None`）
- 网络抖动是爬虫的常态，零重试策略意味着每次运行都有概率永久丢失某个信源的数据

**影响**: 每次运行丢数据概率 = 1 - (1 - p)^n，其中 p 为单次失败率，n 为信源数。即使 p=5%，10 个信源就有约 40% 的概率至少丢失一个。

**修复建议**:
```python
MAX_RETRIES = 3
BASE_DELAY = 1.0  # 秒

async def fetch_feed(session, url, source_name="", timeout=15):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsHotScraper/1.0)"}
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.text()
                elif resp.status == 429:  # Rate limited
                    delay = BASE_DELAY * (2 ** (attempt - 1))
                    print(f"  ⏳ {url}: 429 Rate Limited, retry in {delay}s (attempt {attempt}/{MAX_RETRIES})")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"  ⚠ {url}: HTTP {resp.status} (attempt {attempt}/{MAX_RETRIES})")
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(BASE_DELAY * (2 ** (attempt - 1)))
                        continue
                    return None
        except asyncio.TimeoutError:
            print(f"  ⏱ {url}: Timeout (attempt {attempt}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BASE_DELAY * (2 ** (attempt - 1)))
                continue
            return None
        except aiohttp.ClientError as e:
            print(f"  ✗ {url}: {e} (attempt {attempt}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(BASE_DELAY * (2 ** (attempt - 1)))
                continue
            return None
        except Exception as e:
            print(f"  ✗ {url}: Unexpected error: {e}")
            return None  # 不可恢复错误，不重试
    
    return None
```

---

### S-03: feedparser 解析失败静默丢弃 — 无日志追踪

**位置**: `scrape.py` 第 175-178 行

```python
try:
    feed = feedparser.parse(result)
except Exception:
    continue
```

**问题**:
1. `feedparser.parse()` **实际上不会抛出异常** — 它总是返回一个 `FeedParserDict`，即使内容为空或格式完全错误。所以这个 `except` 块永远不会被触发，是死代码。
2. 当 `feed.bozo == 1` 时（解析警告），没有任何日志输出。`bozo` 标志表示 feed 中有非标准 XML 结构。
3. 完全无效的 HTML/JSON/纯文本被当作 RSS 处理时，`feed.entries` 为空，但没有任何诊断信息。

**影响**: 信源格式变更或 feed 损坏时，完全无感知，数据丢失不被发现。

**修复建议**:
```python
feed = feedparser.parse(result)

# 检查解析警告
if feed.bozo:
    warning = feed.bozo_exception
    exc_type = type(warning).__name__ if warning else "Unknown"
    print(f"  ⚠ {src['name']}: Feed 解析警告 [{exc_type}]: {warning}")

if not feed.entries:
    print(f"  ○ {src['name']}: 无条目 (bozo={feed.bozo})")
    continue
```

---

### S-04: upsert 时 link 为空或重复 — 数据静默丢失

**位置**: `scrape.py` 第 56-59 行 + `db.py` 第 82-101 行

```python
# scrape.py:56-59
title = item.get("title", "").strip()
link = item.get("link", "").strip()
if not title or not link:
    return None

# db.py:82-101
async def upsert_article(...):
    try:
        await db.execute("INSERT INTO articles ...", ...)
    except Exception:
        # 重复链接，增加 source_count
        await db.execute("UPDATE articles SET source_count = source_count + 1 WHERE link = ?", (link,))
```

**问题**:
1. **link 为空直接丢弃**: 第 58-59 行 `if not title or not link: return None` — 如果一个 RSS 条目有标题但没有链接（某些 feed 确实如此），该文章被永久丢弃，无任何日志。
2. **upsert 的 `except` 捕获范围过宽**: `db.py:95` 的 `except Exception` 不仅捕获 `UNIQUE constraint failed`，还捕获数据库连接错误、SQL 语法错误等所有异常，导致**真正的数据库错误被静默吞掉**。
3. **重复文章的 source_count 递增逻辑有误**: `scrape.py:188` 对每个信源的每个条目都调用 `upsert_article`。如果一篇文章被 10 个信源转载，`source_count` 会被设为 10——但这恰好是正确的。然而 `db.py:98` 的 UPDATE 无条件执行（因为 `except` 已经捕获了所有异常），如果 INSERT 因非唯一约束原因失败（如外键冲突），也会错误地执行 UPDATE。

**影响**: 数据库异常被掩盖；RSS 条目丢失无迹可寻；潜在的数据不一致。

**修复建议**:
```python
# scrape.py: 对无 link 的条目记录警告
if not title:
    print(f"  ⚠ {source_name}: 空标题条目，跳过")
    return None
if not link:
    print(f"  ⚠ {source_name}: 条目 '{title}' 无链接，跳过")
    return None

# db.py: 精确捕获唯一约束冲突
import sqlite3
async def upsert_article(...):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO articles (title, link, ...) VALUES (?, ?, ...)",
                (title, link, ...)
            )
        except sqlite3.IntegrityError:
            await db.execute(
                "UPDATE articles SET source_count = source_count + 1 WHERE link = ?",
                (link,)
            )
        except Exception as e:
            # 重新抛出真正的数据库错误
            raise RuntimeError(f"upsert_article failed for link={link}: {e}") from e
        await db.commit()
```

---

## 🟠 高优先级问题 (High)

### H-01: 无增量抓取 — 每次全量抓取所有信源

**位置**: `scrape.py` 第 141-194 行 (`scrape_all`)

**问题**: 
- 每次运行都从所有信源抓取全部内容，没有任何"上次抓取时间"或"已抓取文章指纹"的记录
- `articles` 表的 `link` 字段有 `UNIQUE` 约束，重复文章会被忽略（`source_count++`），但**旧文章永远不会被清理**
- 如果某个信源有 1000 篇历史文章，每次都要下载并解析全部 1000 条
- 浪费带宽、增加反爬风险、拖慢执行速度

**影响**: 随着信源积累的历史文章增多，抓取时间线性增长；大量不必要的 HTTP 请求和 feedparser 解析。

**修复建议**:
```python
# 新增: 记录每个信源的最后抓取时间
async def get_last_fetch_time(db, source_url):
    """获取信源上次抓取时间"""
    ...

# 新增: 保存抓取时间
async def save_fetch_time(db, source_url, timestamp):
    """保存信源抓取时间"""
    ...

# 在 fetch_feed 中添加 If-Modified-Since 头
async def fetch_feed(session, url, source_name="", timeout=15):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsHotScraper/1.0)"}
    
    # 增量: 携带上次抓取时间
    last_time = await get_last_fetch_time(None, url)  # 需要传递 db 连接
    if last_time:
        headers["If-Modified-Since"] = last_time.strftime("%a, %d %b %Y %H:%M:%S GMT")
    
    async with session.get(url, headers=headers, timeout=timeout) as resp:
        if resp.status == 304:
            print(f"  ↻ {url}: 未修改，跳过")
            return None  # 服务端支持条件请求
        if resp.status == 200:
            return await resp.text()
        ...

# 在 scrape_all 中:
# 1. 先获取已有文章的 link 集合
# 2. 只处理新的条目
# 3. 抓取完成后更新最后抓取时间
```

---

### H-02: 无抓取频率控制 — 可能导致信源过载

**位置**: `scrape.py` 第 165-167 行

**问题**: 当前代码一次性抓取所有信源，没有考虑：
- 单个信源两次抓取之间的最小间隔（如 30 分钟）
- 全局抓取频率限制
- 不同信源可能有不同的推荐抓取频率

**影响**: 如果 cron 任务配置为每 10 分钟运行一次，每个信源每 10 分钟就被完整拉取一次，这对大多数 RSS 源来说过于频繁。

**修复建议**:
```python
# 在 sources 表中增加 min_interval 字段
# 或者在 scrape_all 中:
MIN_INTERVAL_SECONDS = 1800  # 30 分钟默认值

for src in sources:
    interval = src.get("min_interval", MIN_INTERVAL_SECONDS)
    last_fetch = await get_last_fetch_time(src["url"])
    if last_fetch and (datetime.now() - last_fetch).total_seconds() < interval:
        print(f"  ⏸ {src['name']}: 距离上次抓取不足 {interval}s，跳过")
        continue
```

---

### H-03: 无信源健康监控 — 长期失效的信源无人知晓

**位置**: `scrape.py` 第 163-193 行

**问题**: 
- 没有记录每个信源的连续失败次数
- 没有检测信源是否长期不可达
- 没有"信源健康度"指标（成功率、平均响应时间、最后成功时间）
- 死信源（如已停更的博客）不会被自动标记或告警

**影响**: 信源可能已经挂了几个月，但用户不知道，以为数据是完整的。

**修复建议**:
```python
# 新增信源健康表
"""
CREATE TABLE IF NOT EXISTS source_health (
    source_url TEXT PRIMARY KEY,
    last_success TIMESTAMP,
    last_failure TIMESTAMP,
    consecutive_failures INTEGER DEFAULT 0,
    total_fetches INTEGER DEFAULT 0,
    total_successes INTEGER DEFAULT 0,
    avg_response_time_ms REAL DEFAULT 0,
    health_score REAL DEFAULT 1.0  -- 0~1, 低于阈值标记为 disabled
)
"""

# 在 fetch_feed 中记录健康指标
async def fetch_feed(session, url, source_name="", timeout=15):
    start = time.monotonic()
    try:
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            elapsed_ms = (time.monotonic() - start) * 1000
            if resp.status == 200:
                await record_health(url, success=True, elapsed_ms=elapsed_ms)
                return await resp.text()
            else:
                await record_health(url, success=False, elapsed_ms=elapsed_ms)
                return None
    except Exception as e:
        await record_health(url, success=False, elapsed_ms=(time.monotonic()-start)*1000)
        return None
```

---

### H-04: 日期解析格式覆盖不全 — 中文 feed 日期丢失

**位置**: `scrape.py` 第 98-109 行

```python
for fmt in [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%a, %d %b %Y %H:%M:%S %z",
]:
```

**问题**: 
1. **缺少 RFC 2822 的变体**: 很多 RSS feed 使用带逗号前缀的 RFC 2822 格式 `%a, %d %b %Y %H:%M:%S %z`，但实际 feed 中的时区偏移可能不带冒号（如 `+0800` vs `+08:00`），`strptime` 无法解析。
2. **缺少 ISO 8601 毫秒级**: `%Y-%m-%dT%H:%M:%S.%f%z` 是常见格式，尤其在国内 feed 中。
3. **缺少 `dateutil` 通用解析**: 硬编码格式列表无法覆盖所有边缘情况。feedparser 本身有 `item.get("published_parsed")` 返回 `time.struct_time`，但代码完全没有利用。
4. **`%z` 在 Python < 3.7 的行为差异**: 虽然当前环境是 3.11，但代码风格应更健壮。

**影响**: 大量文章的 `published_at` 字段为空字符串，导致排序和筛选功能失效。

**修复建议**:
```python
from dateutil import parser as dateutil_parser

# 优先使用 feedparser 提供的结构化日期
published_dt = item.get("published_parsed")
if published_dt:
    try:
        dt = datetime.fromtimestamp(datetime(*published_dt[:6]).timestamp())
        published = dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
elif published_raw:
    # 备用: dateutil 通用解析
    try:
        dt = dateutil_parser.parse(published_raw)
        published = dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError):
        published = ""
```

---

### H-05: `parse_rss_item` 中 HTML2Text 实例重复创建 — 性能浪费

**位置**: `scrape.py` 第 69-72 行

```python
h = html2text.HTML2Text()
h.ignore_links = False
h.ignore_images = True
desc = h.handle(desc) if desc else ""
```

每个条目都创建一个新的 `html2text.HTML2Text()` 实例。如果某信源有 100 篇文章，就创建 100 个实例。

**影响**: 轻微性能损耗，大量条目时累积明显。

**修复建议**: 将 `html2text.HTML2Text()` 实例化移到函数外部或使用单例模式。

---

## 🟡 中等问题 (Medium)

### M-01: 默认信源硬编码在 scrape.py 中 — 与 main.py 重复

**位置**: `scrape.py` 第 149-154 行

```python
defaults = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "资讯"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/", "资讯"),
    ("机器之心", "https://www.jiqizhixin.com/rss", "资讯"),
    ("Hacker News", "https://hnrss.org/frontpage", "产品"),
]
```

**问题**: 根据 CODE_REVIEW_REPORT.md 第 355-358 行，这些默认信源也在 `main.py` 中重复定义。修改信源需要同步两处。

**修复建议**: 提取到独立配置文件 `config.py` 或通过环境变量/数据库管理。

---

### M-02: `CATEGORY_MAP` 硬编码且无持久化

**位置**: `scrape.py` 第 24-37 行

**问题**: 分类映射硬编码在代码中。新增分类需要修改代码并重新部署。

**修复建议**: 将分类映射存储在数据库中，允许运行时更新。

---

### M-03: 无抓取进度/统计日志

**位置**: `scrape.py` 第 165-193 行

**问题**: 整个抓取过程没有进度日志。如果信源多、条目多，用户无法知道：
- 当前抓到第几个信源
- 预计还需要多长时间
- 是否有信源卡住（长时间无响应）

**修复建议**:
```python
import time

async def scrape_all():
    ...
    start_time = time.time()
    
    for idx, (src, result) in enumerate(zip(sources, results), 1):
        elapsed = time.time() - start_time
        eta = (elapsed / idx) * (len(sources) - idx) if idx > 0 else 0
        print(f"[{idx}/{len(sources)}] {src['name']} ({elapsed:.0f}s, ETA: {eta:.0f}s)")
```

---

### M-04: `html2text` 配置不完整 — 长文本截断

**位置**: `scrape.py` 第 69-72 行

```python
h = html2text.HTML2Text()
h.ignore_links = False
h.ignore_images = True
desc = h.handle(desc) if desc else ""
```

虽然设置了 `ignore_links = False`，但 `html2text` 默认 `body_width=70`，长描述会被自动换行截断成多行，与后续的 `desc.strip()[:500]` 配合时，可能在截断处产生奇怪的分行。

**修复建议**:
```python
h = html2text.HTML2Text()
h.ignore_links = False
h.ignore_images = True
h.body_width = 0  # 不自动换行
h.protect_links = True  # 保护链接不被转换
```

---

### M-05: 数据库连接无复用 — SQLite 高并发写入阻塞

**位置**: `db.py` 全部函数 + `scrape.py` 第 188 行

**问题**: 结合 CODE_REVIEW_REPORT.md 第 91-107 行，每个 `upsert_article` 调用都创建新的 SQLite 连接并立即 commit/close。在高并发抓取场景下（每信源 50-200 条），可能产生数百个快速创建/销毁的连接。SQLite 的文件锁机制在高并发写入时会导致阻塞。

**修复建议**: 复用数据库连接或使用连接池，或批量 INSERT+ON CONFLICT UPDATE。

---

### M-06: `normalize_category` 子串匹配可能导致误匹配

**位置**: `scrape.py` 第 48-50 行

```python
for key, val in CATEGORY_MAP.items():
    if key in raw_category.lower():
        return val
```

**问题**: 子串匹配是贪婪的（按 `CATEGORY_MAP` 的遍历顺序），可能导致意外结果。例如，如果 `raw_category = "ai-security"`，它会先匹配 `"ai"` → 返回 `"AI"`，但可能应该匹配 `"security"`（如果存在的话）。

**修复建议**: 优先精确匹配，其次最长子串匹配。

---

## ℹ️ 低优先级/改进建议 (Low)

### L-01: 缺少结构化日志 — 仅用 print

**位置**: `scrape.py` 第 134, 137, 181, 192, 194 行

**问题**: 全部使用 `print()`，无法区分 INFO/WARN/ERROR 级别，无法输出到文件，无法集成日志聚合系统。

**修复建议**: 使用 Python `logging` 模块：
```python
import logging
logger = logging.getLogger("rss_scraper")
logger.warning(f"{url}: HTTP {resp.status}")
logger.error(f"{url}: {e}", exc_info=True)
```

---

### L-02: 缺少 User-Agent 轮换

**位置**: `scrape.py` 第 126-128 行

```python
headers = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsHotScraper/1.0)"
}
```

固定 UA 容易被识别为爬虫。建议从列表中随机选择。

**修复建议**:
```python
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
    "NewsHotScraper/1.0 (+https://yourdomain.com/bot)",
]

headers = {"User-Agent": random.choice(USER_AGENTS)}
```

---

### L-03: 缺少 Feed 内容类型校验

**位置**: `scrape.py` 第 131-132 行

```python
if resp.status == 200:
    return await resp.text()
```

**问题**: 没有检查 `Content-Type` 头。某些 RSS 源可能返回 `application/json`（JSON Feed）或 `text/html`（错误页面）。直接解析 JSON Feed 为 XML 会导致 feedparser 静默返回空结果。

**修复建议**:
```python
content_type = resp.content_type or ""
if "json" in content_type:
    # JSON Feed 需要不同的解析器
    data = await resp.json()
    feed = parse_json_feed(data)  # 自定义函数
elif "xml" in content_type or "rss" in content_type or "atom" in content_type:
    return await resp.text()
else:
    print(f"  ⚠ {url}: 未知 Content-Type: {content_type}")
    return None
```

---

### L-04: 缺少最大响应体大小限制

**位置**: `scrape.py` 第 132 行

```python
return await resp.text()
```

**问题**: 没有限制响应体大小。恶意或配置错误的 RSS 源可能返回 GB 级别的响应，耗尽内存。

**修复建议**:
```python
# 使用 stream 模式限制
reader = resp.content
chunks = []
total_size = 0
MAX_SIZE = 5 * 1024 * 1024  # 5MB
async for chunk in resp.content.iter_chunked(8192):
    total_size += len(chunk)
    if total_size > MAX_SIZE:
        raise aiohttp.PayloadTooLarge("RSS feed exceeds 5MB")
    chunks.append(chunk)
return b"".join(chunks).decode("utf-8", errors="replace")
```

---

### L-05: `scrape_all` 中 total 计数逻辑有歧义

**位置**: `scrape.py` 第 184-190 行

```python
added = 0
for entry in feed.entries:
    article = parse_rss_item(entry, src["name"])
    if article:
        await upsert_article(**article)
        added += 1
        total += 1
```

**问题**: `added` 计数的是"调用了 upsert 的次数"，而不是"真正新增的文章数"。因为 `upsert_article` 在遇到重复 link 时会走 UPDATE 分支（不抛异常），所以 `added` 包含了重复文章。print 语句说 `{added} 条新`，但实际可能大部分是旧的。

**修复建议**: `upsert_article` 应返回布尔值表示是否真正插入新记录。

---

## 总结

### 最紧急的三个修复

1. **S-01 并发限流** — 加 `asyncio.Semaphore(5)` 防止信源反爬
2. **S-02 重试机制** — 实现指数退避，至少重试 3 次
3. **S-04 精确异常捕获** — `db.py` 中 `except Exception` 改为 `except sqlite3.IntegrityError`

### 中长期改进

4. **H-01 增量抓取** — 记录最后抓取时间，利用 `If-Modified-Since`
5. **H-03 信源健康监控** — 新增 `source_health` 表
6. **H-04 日期解析增强** — 引入 `python-dateutil`
7. **L-01 结构化日志** — 迁移到 `logging` 模块
8. **L-03 内容类型校验** — 区分 RSS/XML 与 JSON Feed
9. **L-04 响应体大小限制** — 防止 OOM
