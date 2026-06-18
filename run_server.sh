#!/bin/bash
set -e

cd /e/WorkBuddy/news_hot_site

# 安装依赖
uv pip install uvicorn[aiohttp] aiosqlite aiohttp feedparser html2text fastapi jinja2 -q 2>&1 || true

# 杀掉旧进程
pkill -f "uvicorn.*8084" 2>/dev/null || true
sleep 1

# 启动服务
nohup uv run python -m uvicorn backend.main:app --host 0.0.0.0 --port 8084 > /tmp/news_hot_8084.log 2>&1 &
SERVER_PID=$!
echo "Server started with PID: $SERVER_PID"

# 等待启动
sleep 5

# 测试
RESULT=""
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8084/ 2>/dev/null || echo "000")
RESULT="$RESULT\n首页 HTTP: $HTTP_CODE"

HTTP_CODE2=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8084/curated 2>/dev/null || echo "000")
RESULT="$RESULT\n精选页 HTTP: $HTTP_CODE2"

# 热点 API
HOT_JSON=$(curl -s http://localhost:8084/api/hot 2>/dev/null || echo "FAILED")
RESULT="$RESULT\n热点 API: $HOT_JSON"

# 首页 HTML 前500字符
HOME_HTML=$(curl -s http://localhost:8084/ 2>/dev/null | head -c 500 || echo "FAILED")
RESULT="$RESULT\n首页预览: $HOME_HTML"

echo -e "$RESULT"
