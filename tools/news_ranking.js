/* Same versioned formula as news_ranking.py. Always start from importance. */
(function (global) {
  'use strict';
  const policy = Object.freeze({version: 'progress-decay-v1', grace_hours: 1,
    half_life_hours: 6, floor: 0.5});
  function factor(ageHours, config = policy) {
    return config.floor + (1 - config.floor) * 2 **
      (-Math.max(0, ageHours - config.grace_hours) / config.half_life_hours);
  }
  function progress(node, now, config = policy) {
    const first = Date.parse(node.first_reported_at);
    const importance = Number(node.importance_score);
    if (!Number.isFinite(first) || !Number.isFinite(importance)) return 0;
    const age = Math.max(0, (now - first) / 3600000);
    return Math.round(Math.max(0, importance) * factor(age, config) * 1e8) / 1e8;
  }
  function card(nodes, now, config = policy) {
    return Math.max(0, ...nodes.map(node => progress(node, now, config)));
  }
  function compare(a, b) {
    return b.score - a.score || b.first - a.first ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  }
  global.NEWSHOT_RANKING = Object.freeze({policy, factor, progress, card, compare});
})(globalThis);
