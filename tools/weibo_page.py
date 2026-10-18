"""Build a source page using NEWSHOT's existing page layout."""
from __future__ import annotations

import json
from pathlib import Path
import shutil


def render_weibo_page(output_root: Path) -> None:
    from curated_events import layout

    snapshot = output_root / "weibo" / "latest.json"
    payload = json.loads(snapshot.read_text()) if snapshot.exists() else {
        "ok": True, "items": [], "accounts": [], "count": 0, "fetched_at": None, "window_hours": 24,
    }
    seed = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    body = '''
    <style>
      .weibo-tools { display:flex; flex-wrap:wrap; gap:12px; margin:18px 0; align-items:end; }
      .weibo-tools label { display:grid; gap:6px; font-size:13px; color:var(--muted); }
      .weibo-tools select,.weibo-tools input { min-width:0; width:100%; max-width:100%; min-height:38px; padding:8px 10px; background:#fff; border:1px solid var(--line); border-radius:6px; color:var(--text); }
      .weibo-search { flex:1; min-width:180px; }
      .weibo-card { padding:18px 20px; margin-bottom:12px; }
      .weibo-meta { display:flex; justify-content:space-between; gap:12px; font-size:13px; color:var(--muted); margin-bottom:9px; }
      .weibo-meta strong { color:var(--blue); }
      .weibo-summary { color:#44516a; line-height:1.75; font-size:14px; white-space:pre-wrap; overflow-wrap:anywhere; }
      .weibo-footer { display:flex; align-items:center; flex-wrap:wrap; gap:10px; color:var(--muted); font-size:12px; }
      .weibo-footer a { margin-left:auto; color:var(--blue); }
      .weibo-empty { padding:32px 20px; color:var(--muted); }
      .weibo-warning { color:#9a6400; }
      #weibo-last-sync { font-size:17px; }
      @media(max-width:600px) { .weibo-tools label { flex:1; min-width:40%; } .weibo-card { padding:15px; } .weibo-meta { font-size:12px; } }
    </style>
    <div class="page-head category-page-head">
      <h1>关注微博</h1>
      <div class="sub">你指定账号发布的公开动态，保留来源和原文链接。这里与微博热搜分开，长微博可能为摘要。</div>
    </div>
    <div class="category-summary">
      <div class="summary-cell"><strong id="weibo-count">--</strong><span>当前显示 / 缓存动态</span></div>
      <div class="summary-cell"><strong id="weibo-account-count">--</strong><span>关注账号</span></div>
      <div class="summary-cell"><strong id="weibo-last-sync">--</strong><span>本机最近检查（北京时间）</span></div>
      <div class="summary-cell"><strong id="weibo-window">近 24 小时</strong><span>保留原发布时间</span></div>
    </div>
    <div class="weibo-tools">
      <label>账号<select id="weibo-account" aria-label="筛选微博账号"></select></label>
      <label>领域<select id="weibo-category" aria-label="筛选新闻领域"></select></label>
      <label>排序<select id="weibo-order"><option value="time">发布时间倒序</option><option value="engagement">互动量优先</option></select></label>
      <label class="weibo-search">搜索<input id="weibo-query" type="search" placeholder="标题、摘要或账号名"></label>
      <button id="weibo-refresh" type="button" class="control-btn live-refresh-btn">刷新网站缓存</button>
    </div>
    <p id="weibo-status" class="live-refresh-status" role="status" aria-live="polite"></p>
    <div id="weibo-list" aria-live="polite"></div>
    <p class="sub">刷新本页只读取网站缓存，最近检查时间由采集端提供。Mac 休眠或采集异常时会显示更新提示。</p>
    <script id="weibo-seed" type="application/json">SEED_JSON</script>
    <script src="/assets/weibo-feed.js" defer></script>
    '''.replace("SEED_JSON", seed)
    page = output_root / "hotlists" / "weibo" / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(layout("关注微博 · NEWSHOT", body, "weibo"), encoding="utf-8")
    assets = output_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "cloudflare" / "pages" / "weibo-feed.js", assets / "weibo-feed.js")
