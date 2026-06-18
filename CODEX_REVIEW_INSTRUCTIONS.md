# Codex 审查指令：新闻热点项目 (news_hot_site) 发布前审查

## 项目概况

**路径**: `E:\WorkBuddy\news_hot_site\`
**架构**: FastAPI + SQLite + RSS 爬虫 + Jinja2 前端
**运行**: 端口 8084，当前服务器正在运行 (PID 16884)
**Python**: `.venv\` 虚拟环境

## 当前状态

### 数据
- 文章总数: 200 篇
- 信源数: 11 个活跃信源
- 翻译覆盖率: 标题 100%, 描述 32%
- 有热点分数: 152 篇
- 精选文章: 0 篇
- 未来时间文章: 0
- source_count > 10: 0
- 日期范围: 2026-06-12 ~ 2026-06-18

### 已修复的历史问题
- source_count 虚高 → 改为信源名去重匹配
- published_at 未来时间 → UTC+1h 容差校验
- 翻译重复浪费 → 抓取前查 DB 跳过已翻译
- 热点分数滞后 → scrape_all 末尾自动调用 calculate_hot_scores()
- 品玩失效信源 → 已移除

## 审查任务

### P0 - 必须修复（影响发布）

#### 1. 精选页面 (/curated) 始终为空
- **现状**: 数据库 `is_featured` 字段为 0，没有自动精选机制
- **要求**: 实现自动精选逻辑 —— 选取热点分数最高的前 10-20 篇文章标记为 featured
- **实现方式**: 在 `scrape_all()` 末尾，调用 `calculate_hot_scores()` 之后，选 top-N 标记 `is_featured=1`
- **参考**: `backend/db.py` 已有 `set_featured()` 函数，只需在抓取流程中调用

#### 2. 前端描述截断无展开功能
- **现状**: `index.html` 第 154 行 `article.translated_desc[:200]` 截断到 200 字符，无任何展开按钮
- **要求**: 加展开/收起按钮，点击后显示完整描述
- **实现方式**: 在 `article-desc` div 后加 `<button class="expand-btn">展开</button>`，JS 控制显示完整内容

### P1 - 应该修复（影响体验）

#### 3. 清理备份文件
以下文件应删除或加入 `.gitignore`：
- `backend/*.bak`, `backend/*.pre_review`
- `frontend/*.bak`, `frontend/*.pre_review`
- `frontend/static/css/*.bak`, `frontend/static/css/*.pre_review`
- `scraper/*.bak`, `scraper/*.pre_review`, `scraper/*.fixed`

#### 4. 翻译描述覆盖率低 (32%)
- **现状**: 只有 64/200 篇文章有翻译描述
- **原因**: `scrape.py` 第 434 行只翻译非中文的描述，但很多文章的原始描述本身就是中文或空的
- **建议**: 这是正常现象，中文源的文章描述不需要翻译。但如果用户希望提升覆盖率，可以增加一个"补翻译"API 端点，对缺失翻译描述的文章进行补全

#### 5. 机器之心信源可能已失效
- **现状**: 机器之心 RSS (`jiqizhixin.com/rss`) 在之前的审查中被标记为可能失效
- **建议**: Codex 应该实际测试这个信源的可达性，如果持续失败应移除

### P2 - 可选优化

#### 6. 首页缺少站点统计信息
- **建议**: 在首页底部或侧边显示：文章总数、信源数、更新日期等统计信息

#### 7. 缺少 `.gitignore`
- **现状**: 项目中似乎没有 `.gitignore`
- **建议**: 添加标准 Python + Next.js 混合项目的 `.gitignore`

#### 8. 健康检查端点 `/health` 可改进
- **现状**: 返回基本统计
- **建议**: 增加各信源健康状态明细

## 审查步骤

1. **代码审查**: 逐个阅读 `backend/main.py`, `backend/db.py`, `scraper/scrape.py`, `backend/translator.py`, 前端模板和 CSS
2. **数据验证**: 运行数据库查询确认统计数据
3. **功能测试**: 访问 `http://localhost:8084/` 和各页面，确认渲染正常
4. **信源测试**: 实际测试每个信源的 RSS 可达性
5. **修复实施**: 按 P0 → P1 → P2 优先级实施修复
6. **回归验证**: 修复后重启服务器并验证

## 注意事项

- **Windows 环境**: 使用 git-bash/POSIX 语法，不要用 PowerShell/cmd
- **路径**: 所有文件落盘 `E:\` 盘
- **数据库**: `data/news.db`，使用 aiosqlite 异步操作
- **翻译**: 使用 Google Translate 免费 API，无需 API Key
- **不要**: 修改 `.venv\` 中的包，不要动 `data\server-8084.out.log` / `err.log`
- **重启服务器**: 先 kill 旧进程，然后 `cd E:\WorkBuddy\news_hot_site && .venv\Scripts\python -m uvicorn backend.main:app --host 0.0.0.0 --port 8084`

## 交付要求

审查完成后，输出一份完整的审查报告，包含：
1. 发现的问题清单（按 P0/P1/P2 分级）
2. 每个问题的详细分析和修复方案
3. 修复后的验证结果
4. 最终是否可以发布的结论

报告保存为 `E:\WorkBuddy\news_hot_site\REVIEW_REPORT.md`
