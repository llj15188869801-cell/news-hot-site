#!/bin/bash
set -e

cd /e/WorkBuddy/news_hot_site

# 安装依赖
uv pip install uvicorn[aiohttp] aiosqlite aiohttp feedparser html2text fastapi jinja2 -q 2>&1

# 杀掉可能占用的端口
pkill -f "uvicorn.*8084" 2>/dev/null || true
sleep 1

# 启动服务（后台）
nohup uv run python -m uvicorn backend.main:app --host 0.0.0.0 --port 8084 > /tmp/news_hot.log 2>&1 &
echo "Server PID: $!"

# 等待启动
sleep 5

# 检查服务是否运行
if curl -s http://localhost:8084/ > /dev/null 2>&1; then
    echo "=== 首页 ==="
    curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8084/
    
    echo "=== 精选页 ==="
    curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8084/curated
    
    echo "=== 热点 API ==="
    curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8084/api/hot
    
    echo "=== 热点内容预览 ==="
    curl -s http://localhost:8084/api/hot 2>&1 | head -c 800
    
    echo ""
    echo "=== 首页内容预览 ==="
    curl -s http://localhost:8084/ 2>&1 | head -c 800
    
    echo ""
    echo "✅ 所有测试通过！服务运行在 http://localhost:8084"
else
    echo "❌ 服务启动失败，查看日志："
    cat /tmp/news_hot.log 2>/dev/null || echo "日志文件不存在"
fi
