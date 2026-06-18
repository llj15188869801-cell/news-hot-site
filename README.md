# 新闻热点

FastAPI + SQLite + RSS 的本地采集站，以及用于公开发布的 Cloudflare Worker 快照。

## 本地服务

```powershell
.venv\Scripts\python -m uvicorn backend.main:app --host 0.0.0.0 --port 8084
```

## 更新线上快照

先完成 RSS 抓取并确认 `data/news.db`，然后执行：

```powershell
npm run export:data
npm run build
npm run deploy
```

`scripts/export_worker_data.py` 会把当前 SQLite 内容导出到 `dist/articles.json`。Cloudflare Worker 提供首页、精选、搜索、详情、信源、健康检查和 JSON API。

## 线上地址

- 首页：https://news-hot.caixunradar.workers.dev/
- 健康检查：https://news-hot.caixunradar.workers.dev/health
- 精选 API：https://news-hot.caixunradar.workers.dev/api/featured
