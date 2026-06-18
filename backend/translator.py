"""
轻量级在线翻译器 - 使用 Google Translate API (免费, 无需 API Key)
将英文内容翻译为简体中文。
"""

import asyncio
import time
import threading
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

# Global executor for non-blocking translation calls
_translate_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="translator")

# Rate limiter for sync calls
_sync_lock = threading.Lock()
_last_translate_time = 0.0
_MIN_TRANSLATE_INTERVAL = 0.35  # ~2.8 requests/sec to avoid 429


def _rate_limit():
    """Simple rate limiter to avoid Google Translate 429."""
    with _sync_lock:
        global _last_translate_time
        now = time.monotonic()
        elapsed = now - _last_translate_time
        if elapsed < _MIN_TRANSLATE_INTERVAL:
            time.sleep(_MIN_TRANSLATE_INTERVAL - elapsed)
        _last_translate_time = time.monotonic()


async def translate_text(text: str, sl: str = "en", tl: str = "zh-CN") -> Optional[str]:
    """异步翻译函数"""
    if not text:
        return None
    
    # 检查是否已有中文
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in text)
    if has_chinese:
        return text
    
    # 过长的文本截断（Google Translate free API limit）
    max_len = 5000
    truncated = False
    if len(text) > max_len:
        text = text[:max_len]
        truncated = True
    
    try:
        import urllib.parse
        import urllib.request
        import json
        
        encoded = urllib.parse.quote(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tl}&dt=t&q={encoded}"
        req = urllib.request.Request(url, headers={"User-Agent": "NewsHot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and data[0]:
                parts = [part[0] for part in data[0] if part[0]]
                result = "".join(parts).strip()
                if result:
                    return result
        return None
    except urllib.error.HTTPError as e:
        print(f"  ⚠ 翻译 HTTP 错误 {e.code}: {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"  ⚠ 翻译 URL 错误: {e.reason}")
        return None
    except Exception as e:
        print(f"  ⚠ 翻译失败: {e}")
        return None


def translate_text_sync(text: str, sl: str = "en", tl: str = "zh-CN") -> Optional[str]:
    """同步版本 — 在新线程中运行 asyncio.run()，完全隔离 event loop"""
    if not text:
        return None
    
    # 检查是否已有中文
    has_chinese = any("\u4e00" <= c <= "\u9fff" for c in text)
    if has_chinese:
        return text
    
    _rate_limit()
    
    # BUG FIX: 原代码用 loop.run_in_executor() 返回 asyncio.Future 而非实际结果
    # 在新线程中运行 asyncio.run() 是最安全的做法
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_sync_translate_worker, text, sl, tl).result(timeout=20)


def _sync_translate_worker(text: str, sl: str, tl: str) -> Optional[str]:
    """在独立线程中运行 asyncio.run()，安全隔离"""
    return asyncio.run(translate_text(text, sl, tl))


if __name__ == "__main__":
    test = "Google DeepMind is worried about what happens when millions of agents start to interact"
    print(f"Original: {test}")
    result = translate_text_sync(test)
    print(f"Translated: {result}")
