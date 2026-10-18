(() => {
  const panel = document.getElementById('home-refresh-panel');
  const button = document.getElementById('home-refresh-button');
  const status = document.getElementById('home-refresh-status');
  const progress = document.getElementById('home-refresh-progress');
  const details = document.getElementById('home-refresh-details');
  const summary = document.getElementById('home-refresh-summary');
  const sources = document.getElementById('home-refresh-sources');
  const labels = {pending:'等待', running:'采集中', updated:'已更新', cached:'用缓存', partial:'部分更新', failed:'失败', paused:'已暂停', disabled:'未启用'};
  let nonce = '', timer, inFlight = false, active = false, retryAt = 0;
  const format = value => {
    const date = new Date(value);
    return value && Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '未知';
  };
  function render(job, cooldown = 600) {
    active = !!job && !job.terminal;
    retryAt = job?.finished_at ? Date.parse(job.finished_at) + cooldown * 1000 : 0;
    button.disabled = active || Date.now() < retryAt;
    button.textContent = active ? '更新中…' : Date.now() < retryAt ? '冷却中' : '更新领域分类';
    if (!job) {
      status.textContent = '后台已连接。点击更新后，各来源按限频采集，先发布公共来源，再补充微博。';
      return;
    }
    status.textContent = job.message + (Date.now() < retryAt ? ' 下一轮可发起时间：' + format(retryAt) : '');
    progress.hidden = !active;
    progress.max = Math.max(job.total, 1);
    progress.value = job.done;
    details.hidden = false;
    summary.textContent = `来源进度 ${job.done}/${job.total} · 最近状态 ${format(job.updated_at)}`;
    sources.replaceChildren();
    for (const source of job.sources) {
      const row = document.createElement('div'); row.className = 'refresh-source';
      for (const text of [source.name, labels[source.state] || source.state, source.message]) {
        const span = document.createElement('span'); span.textContent = text; row.append(span);
      }
      const time = document.createElement('small');
      time.textContent = `实际采集：${format(source.fetched_at)}` + (source.count == null ? '' : ` · ${source.count}条`) + (source.next_allowed_at ? ` · 最早可重试 ${format(source.next_allowed_at)}` : '');
      row.append(time); sources.append(row);
    }
    const generation = job.published_generation;
    const seen = 'newshot-refresh-generation:' + job.id;
    if (generation && Date.parse(generation) > Date.parse(panel.dataset.generation) && sessionStorage.getItem(seen) !== generation) {
      sessionStorage.setItem(seen, generation);
      location.reload();
    }
  }
  async function poll() {
    if (inFlight) return;
    inFlight = true;
    try {
      const response = await fetch('/api/refresh/status', {cache:'no-store', signal:AbortSignal.timeout(12000)});
      if (!(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('更新接口不可用，请确认本机后台服务。');
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || '后台暂不可用。');
      nonce = result.nonce;
      render(result.job, result.cooldown_seconds);
    } catch (error) {
      button.disabled = true; button.textContent = '后台未连接';
      status.textContent = error.message + ' 原有分类和缓存不受影响。';
    } finally {
      inFlight = false;
      clearTimeout(timer); timer = setTimeout(poll, active || Date.now() < retryAt ? 5000 : 30000);
    }
  }
  button.addEventListener('click', async () => {
    if (!nonce || inFlight || active || Date.now() < retryAt) return;
    clearTimeout(timer); inFlight = true; button.disabled = true;
    button.textContent = '提交中…';
    try {
      const response = await fetch('/api/refresh/start', {method:'POST', cache:'no-store',
        headers:{'Content-Type':'application/json', 'X-Refresh-Nonce':nonce}, body:'{}', signal:AbortSignal.timeout(12000)});
      if (!(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('任务提交未确认，请查看后台状态后重试。');
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || '任务提交未确认，请查看后台状态后重试。');
      render(result.job); details.open = true;
    } catch (error) {
      status.textContent = error.message + ' 不会自动重复提交。';
    } finally {
      inFlight = false; timer = setTimeout(poll, 1000);
    }
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearTimeout(timer); poll(); } });
  poll();
})();
