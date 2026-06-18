import snapshot from "./dist/articles.json";

type Article = {
  id: number;
  title: string;
  link: string;
  description: string;
  author: string;
  source_name: string;
  category: string;
  published_at: string;
  hot_score: number;
  tags: string;
  is_featured: number;
  source_count: number;
  translated_title: string;
  translated_desc: string;
};

type Source = {
  name: string;
  url: string;
  category: string;
  last_fetched: string | null;
  fetch_success: number | null;
};

const articles = snapshot.articles as Article[];
const sources = snapshot.sources as Source[];

function escapeHtml(value: unknown) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function displayTitle(article: Article) {
  return article.translated_title || article.title;
}

function displayDescription(article: Article) {
  return article.translated_desc || article.description || "";
}

function formatTime(value: string) {
  if (!value) return "";
  const normalized = value.includes("T") ? value : value.replace(" ", "T") + "Z";
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(date);
}

const styles = `
:root{--bg:#0d1117;--card:#161b22;--hover:#1c2333;--text:#e6edf3;--muted:#8b949e;--accent:#58a6ff;--border:#30363d;--tag:#1f2a38;--green:#3fb950;--gold:#d29922;--hot:#c084fc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.6}a{color:var(--accent);text-decoration:none}a:hover{color:#79b8ff}
header{position:sticky;top:0;z-index:10;background:var(--card);border-bottom:1px solid var(--border)}.nav{max-width:860px;margin:auto;padding:12px 16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}.brand{font-size:20px;font-weight:800;color:var(--text)}.brand b{color:#da3633}.links{display:flex;gap:4px;margin-left:auto}.links a{color:var(--muted);padding:6px 10px;border-radius:6px}.links a:hover{background:var(--hover);color:var(--text)}
.search{display:flex;flex:1;max-width:330px}.search input{min-width:0;flex:1;padding:8px 10px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px 0 0 6px}.search button{padding:8px 12px;background:var(--accent);color:#fff;border:0;border-radius:0 6px 6px 0}.container{max-width:860px;margin:auto;padding:18px 16px}.hero{padding:22px 0 10px}.hero h1{margin:0 0 8px;font-size:28px}.hero p,.muted{color:var(--muted)}
.hot{background:#1a1225;border:1px solid #7c3aed;border-radius:10px;padding:16px;margin-bottom:18px}.hot h2{font-size:18px;color:var(--hot);margin:0 0 10px}.hot ol{margin:0;padding-left:28px}.hot li{padding:5px 0}.hot a{color:var(--text)}
.filters{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0 18px}.filters a{padding:4px 11px;border-radius:15px;background:var(--tag);font-size:13px}.card{display:block;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:9px;color:var(--text)}.card:hover{background:var(--hover);color:var(--text)}.meta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:12px}.tag{padding:2px 8px;border-radius:10px;background:var(--tag);color:#79c0ff}.source{color:var(--green)}.featured{color:var(--gold)}.title{font-size:17px;font-weight:650;margin:7px 0 5px}.desc{color:var(--muted);font-size:13px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.desc.expanded{display:block}.expand{margin-top:7px;padding:3px 9px;background:var(--tag);color:#79c0ff;border:1px solid var(--border);border-radius:4px;cursor:pointer}
.detail{background:var(--card);border:1px solid var(--border);border-radius:9px;padding:24px;margin-top:18px}.detail h1{font-size:28px;line-height:1.35}.detail .body{white-space:pre-wrap;color:#c9d1d9}.original{display:inline-block;margin-top:20px;padding:9px 14px;border:1px solid var(--border);border-radius:6px}.stats{margin-top:18px;padding:14px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--muted)}.empty{text-align:center;padding:55px 16px;color:var(--muted)}footer{max-width:860px;margin:28px auto 0;padding:24px 16px;border-top:1px solid var(--border);text-align:center;color:var(--muted);font-size:12px}
@media(max-width:640px){.nav{align-items:stretch}.search{order:3;max-width:none;flex-basis:100%}.links{margin-left:0;overflow:auto}.hero h1,.detail h1{font-size:23px}}
`;

function layout(title: string, body: string) {
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="AI 热点聚合平台"><title>${escapeHtml(title)} | 新闻热点</title><style>${styles}</style></head><body>
  <header><div class="nav"><a class="brand" href="/"><b>新闻</b>热点</a><form class="search" action="/search"><input name="q" maxlength="200" placeholder="搜索标题、摘要或信源"><button>搜索</button></form><nav class="links"><a href="/">首页</a><a href="/curated">精选</a><a href="/sources">信源</a></nav></div></header>
  <main class="container">${body}</main><footer>新闻热点 · 多信源 RSS 聚合 · 快照生成于 ${escapeHtml(snapshot.generated_at)}</footer>
  <script>document.addEventListener('click',function(e){var b=e.target.closest('.expand');if(!b)return;var d=b.previousElementSibling;d.classList.toggle('expanded');b.textContent=d.classList.contains('expanded')?'收起':'展开';});</script></body></html>`;
}

function card(article: Article) {
  const description = displayDescription(article);
  return `<article class="card"><div class="meta"><span>${formatTime(article.published_at)}</span>${article.category ? `<span class="tag">${escapeHtml(article.category)}</span>` : ""}<span class="source">${escapeHtml(article.source_name)}</span>${article.is_featured ? '<span class="featured">精选</span>' : ""}<span>热度 ${Number(article.hot_score || 0).toFixed(1)}</span></div><h2 class="title"><a href="/article/${article.id}">${escapeHtml(displayTitle(article))}</a></h2>${description ? `<div class="desc">${escapeHtml(description)}</div>${description.length > 200 ? '<button class="expand">展开</button>' : ""}` : ""}</article>`;
}

function homepage(url: URL) {
  const category = (url.searchParams.get("category") || "").trim();
  const categories = [...new Set(articles.map((article) => article.category).filter(Boolean))];
  const visible = (category ? articles.filter((article) => article.category === category) : articles).slice(0, 100);
  const hot = [...articles].sort((a, b) => b.hot_score - a.hot_score).slice(0, 10);
  return layout("首页", `<section class="hero"><h1>全球 AI 与科技热点</h1><p>聚合公开 RSS 信源，按热度排序，并保留原文链接。</p></section><section class="hot"><h2>当前热点</h2><ol>${hot.map((article) => `<li><a href="/article/${article.id}">${escapeHtml(displayTitle(article))}</a></li>`).join("")}</ol></section><nav class="filters"><a href="/">全部</a>${categories.map((item) => `<a href="/?category=${encodeURIComponent(item)}">${escapeHtml(item)}</a>`).join("")}</nav>${visible.map(card).join("")}<div class="stats">共收录 ${articles.length} 篇文章 · ${sources.length} 个信源 · ${articles.filter((article) => article.is_featured).length} 篇精选</div>`);
}

function curated() {
  const featured = articles.filter((article) => article.is_featured).slice(0, 50);
  return layout("精选", `<section class="hero"><h1>精选热点</h1><p>按热点分数自动选出的重点内容。</p></section>${featured.length ? featured.map(card).join("") : '<div class="empty">暂无精选内容</div>'}`);
}

function search(url: URL) {
  const query = (url.searchParams.get("q") || "").trim().slice(0, 200);
  const lowered = query.toLocaleLowerCase("zh-CN");
  const results = query ? articles.filter((article) => [article.title, article.translated_title, article.description, article.translated_desc, article.source_name, article.category].join(" ").toLocaleLowerCase("zh-CN").includes(lowered)).slice(0, 100) : [];
  return layout("搜索", `<section class="hero"><h1>搜索热点</h1><p>${query ? `“${escapeHtml(query)}” 找到 ${results.length} 条结果` : "输入关键词搜索标题、摘要、分类和信源。"}</p></section>${results.map(card).join("")}`);
}

function articleDetail(id: number) {
  const article = articles.find((item) => item.id === id);
  if (!article) return layout("未找到", '<div class="empty">文章不存在或已经下线</div>');
  return layout(displayTitle(article), `<article class="detail"><a href="/">返回首页</a><h1>${escapeHtml(displayTitle(article))}</h1><div class="meta"><span>${formatTime(article.published_at)}</span><span class="source">${escapeHtml(article.source_name)}</span>${article.category ? `<span class="tag">${escapeHtml(article.category)}</span>` : ""}<span>热度 ${Number(article.hot_score || 0).toFixed(1)}</span></div><div class="body">${escapeHtml(displayDescription(article))}</div><a class="original" href="${escapeHtml(article.link)}" target="_blank" rel="noopener noreferrer">阅读原文</a></article>`);
}

function sourcesPage() {
  return layout("信源", `<section class="hero"><h1>RSS 信源状态</h1><p>当前启用 ${sources.length} 个公开信源。</p></section>${sources.map((source) => `<a class="card" href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer"><h2 class="title">${escapeHtml(source.name)}</h2><div class="meta"><span class="tag">${escapeHtml(source.category)}</span><span>${source.fetch_success ? "最近抓取成功" : "尚未确认"}</span><span>${escapeHtml(source.last_fetched || "")}</span></div></a>`).join("")}`);
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "public, max-age=300" } });
}

function html(body: string, status = 200) {
  return new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=300" } });
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (path === "/") return html(homepage(url));
    if (path === "/curated") return html(curated());
    if (path === "/search") return html(search(url));
    if (path === "/sources") return html(sourcesPage());
    if (path.startsWith("/article/")) return html(articleDetail(Number(path.slice(9))));
    if (path === "/api/articles") return json({ articles });
    if (path === "/api/featured") return json({ articles: articles.filter((article) => article.is_featured) });
    if (path === "/health") return json({ status: "ok", version: "worker-1.0.0", generated_at: snapshot.generated_at, database: { total_articles: articles.length, total_sources: sources.length, featured_articles: articles.filter((article) => article.is_featured).length, source_health: { total_sources: sources.length, success_count: sources.filter((source) => source.fetch_success).length, failed_count: sources.filter((source) => source.fetch_success === 0).length } } });
    return html(layout("页面不存在", '<div class="empty">页面不存在，<a href="/">返回首页</a></div>'), 404);
  },
};
