(() => {
  const seed = document.getElementById('weibo-seed');
  const list = document.getElementById('weibo-list');
  const account = document.getElementById('weibo-account');
  const category = document.getElementById('weibo-category');
  const query = document.getElementById('weibo-query');
  const order = document.getElementById('weibo-order');
  const status = document.getElementById('weibo-status');
  const refreshButton = document.getElementById('weibo-refresh');
  let payload = JSON.parse(seed.textContent);
  let loading = false;
  const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
  const formatTime = value => {
    if (!value) return '尚未同步';
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? '时间未知' : new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date);
  };
  const safeUrl = value => /^https:\/\/m\.weibo\.cn\/detail\/\d+$/.test(value || '') ? value : '#';

  function options() {
    const previous = account.value;
    account.replaceChildren(new Option('全部账号', ''));
    for (const user of payload.accounts || []) account.add(new Option(`${user.name}（${user.count || 0}）`, user.id));
    if ([...account.options].some(option => option.value === previous)) account.value = previous;
    const previousCategory = category.value;
    category.replaceChildren(new Option('全部领域', ''));
    const categories = new Map((payload.items || []).map(item => [item.category || 'general', item.category_name || '综合']));
    for (const [id, name] of categories) category.add(new Option(name, id));
    if ([...category.options].some(option => option.value === previousCategory)) category.value = previousCategory;
  }

  function render() {
    const needle = query.value.trim().toLowerCase();
    const items = (payload.items || []).filter(item =>
      item.timestamp >= Date.now() - (payload.window_hours || 24) * 3600 * 1000 &&
      (!account.value || item.account_id === account.value) &&
      (!category.value || item.category === category.value) &&
      (!needle || `${item.title} ${item.summary} ${item.account_name}`.toLowerCase().includes(needle))
    );
    const heat = item => Number(item.likes || 0) + Number(item.comments || 0) + Number(item.reposts || 0);
    items.sort((left, right) => order.value === 'engagement'
      ? heat(right) - heat(left) || right.timestamp - left.timestamp
      : right.timestamp - left.timestamp);
    document.getElementById('weibo-count').textContent = `${items.length} / ${(payload.items || []).length}`;
    document.getElementById('weibo-account-count').textContent = (payload.accounts || []).length;
    document.getElementById('weibo-last-sync').textContent = formatTime(payload.fetched_at);
    document.getElementById('weibo-window').textContent = `近 ${payload.window_hours || 24} 小时`;
    list.innerHTML = items.length ? items.map(item => {
      const summary = String(item.summary || '');
      const preview = summary.length > 220 ? summary.slice(0, 220) + '…' : summary;
      return `<article class="weibo-card panel">
        <div class="weibo-meta"><strong>${escape(item.account_name)}</strong><time datetime="${escape(item.published_at)}">${escape(formatTime(item.timestamp))}</time></div>
        <h2><a href="${escape(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">${escape(item.title)}</a></h2>
        <p class="weibo-summary">${escape(preview)}</p>
        ${summary.length > 220 ? `<details><summary>展开摘要</summary><p class="weibo-summary">${escape(summary)}</p></details>` : ''}
        <div class="weibo-footer"><span class="mini-chip hot">${escape(item.category_name || '综合')}</span>
          ${item.is_retweet ? '<span class="mini-chip">转发</span>' : ''}
          <span>赞 ${Number(item.likes || 0)} · 评论 ${Number(item.comments || 0)} · 转发 ${Number(item.reposts || 0)}</span>
          <a href="${escape(safeUrl(item.url))}" target="_blank" rel="noopener noreferrer">查看微博原文 ↗</a>
        </div></article>`;
    }).join('') : '<div class="panel weibo-empty">没有符合条件的动态，可切换账号、领域或搜索词。</div>';
    const fetched = Date.parse(payload.fetched_at || '');
    const stale = !Number.isFinite(fetched) || Date.now() - fetched > 2 * 3600 * 1000;
    const messages = [];
    if (stale) messages.push('本机采集已超过 2 小时未更新，当前展示最近一次成功同步的缓存。');
    if (payload.collector_error) messages.push('采集提示：' + payload.collector_error);
    if (payload.truncated) messages.push('本次导出达到数量上限，当前时间窗口可能未完整覆盖。');
    status.textContent = messages.join(' ') || `已同步 ${payload.count || 0} 条；动态按原发布时间展示。`;
    status.classList.toggle('weibo-warning', messages.length > 0);
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    refreshButton.disabled = true;
    try {
      const response = await fetch('/api/weibo/followed', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const next = await response.json();
      if (!next.ok || !Array.isArray(next.items) || !Array.isArray(next.accounts)) throw new Error('同步数据不可用');
      payload = next;
      options();
      render();
    } catch (error) {
      render();
      status.textContent = `暂时无法读取网站最新缓存，保留现有内容。${error.message}`;
      status.classList.add('weibo-warning');
    } finally {
      loading = false;
      refreshButton.disabled = false;
    }
  }

  for (const control of [account, category, order]) control.addEventListener('change', render);
  query.addEventListener('input', render);
  refreshButton.addEventListener('click', refresh);
  options();
  render();
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 60_000);
})();
