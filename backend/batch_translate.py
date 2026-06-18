"""批量翻译已有文章"""
import sys
sys.path.insert(0, r"E:\WorkBuddy\news_hot_site\backend")

import asyncio
from db import get_articles, upsert_article
from translator import translate_text_sync

async def main():
    articles = await get_articles(limit=200)
    print(f"找到 {len(articles)} 篇文章待翻译")
    
    translated = 0
    skipped = 0
    
    for a in articles:
        tt = a.get("translated_title")
        td = a.get("translated_desc")
        
        # 已有翻译则跳过
        if tt and td:
            skipped += 1
            continue
        
        title = a.get("title", "")
        desc = a.get("description", "") or ""
        
        # 检查是否已经是中文
        has_cn = any("\u4e00" <= c <= "\u9fff" for c in title)
        if has_cn:
            skipped += 1
            continue
        
        new_tt = translate_text_sync(title) if title else ""
        new_td = translate_text_sync(desc) if desc else ""
        
        if new_tt or new_td:
            await upsert_article(
                title=title,
                link=a.get("link", ""),
                translated_title=new_tt or "",
                translated_desc=new_td or "",
            )
            translated += 1
            if translated % 10 == 0:
                print(f"  已翻译 {translated} 篇...")
    
    print(f"\n翻译完成: 成功 {translated} 篇, 跳过 {skipped} 篇")

asyncio.run(main())
