"""Progressive local refresh control for the classified home."""
from html import escape
from pathlib import Path


def markup(generation):
    script = Path(__file__).with_name("home_refresh.js").read_text()
    return """
    <style>
      .category-page-head > div:first-child { width:100%; }
      .home-title-line { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
      .home-title-line h1 { margin-bottom:0; }
      #home-refresh-button { padding:9px 15px; min-height:42px; border:1px solid #067ba4; border-radius:6px; color:#07678b; background:#fff; font:inherit; font-weight:600; cursor:pointer; }
      #home-refresh-button:hover:not(:disabled) { background:#eef8fc; }
      #home-refresh-button:disabled { opacity:.65; cursor:default; }
      #home-refresh-button:focus-visible, #home-refresh-panel summary:focus-visible { outline:3px solid #80cdea; outline-offset:3px; }
      #home-refresh-panel { margin:0 0 16px; padding:12px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); color:var(--muted); font-size:13px; line-height:1.65; }
      #home-refresh-status { margin:0; color:var(--ink); overflow-wrap:anywhere; }
      #home-refresh-progress { display:block; width:min(400px,100%); height:6px; margin:10px 0; accent-color:#087fa7; }
      #home-refresh-progress[hidden] { display:none; }
      #home-refresh-panel summary { cursor:pointer; width:fit-content; }
      #home-refresh-sources { margin-top:8px; }
      .refresh-source { display:grid; grid-template-columns:minmax(110px,160px) 70px 1fr; gap:4px 12px; padding:7px 0; border-top:1px solid var(--line); }
      .refresh-source span { overflow-wrap:anywhere; }
      .refresh-source small { grid-column:3; color:var(--muted); }
      @media(max-width:600px) { .home-title-line { gap:10px; } #home-refresh-button { font-size:13px; } .refresh-source { grid-template-columns:1fr 70px; } .refresh-source span:nth-child(3), .refresh-source small { grid-column:1 / -1; } }
    </style>
    <section id="home-refresh-panel" data-generation=""" + '"' + escape(generation, quote=True) + '"' + """ aria-label="领域分类更新进度">
      <p id="home-refresh-status" role="status" aria-live="polite">正在连接更新后台…</p>
      <progress id="home-refresh-progress" value="0" max="1" hidden aria-label="来源更新进度"></progress>
      <details id="home-refresh-details" hidden><summary id="home-refresh-summary">查看来源进度</summary><div id="home-refresh-sources"></div></details>
      <div>后台分阶段更新，可关闭页面后回来查看。公共来源通常较快，微博按账号限频排队，可能超过5分钟；公众号仍停采。</div>
    </section>
    <script>""" + script + """</script>"""
