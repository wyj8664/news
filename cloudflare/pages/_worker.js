const CHINA_TZ = "Asia/Shanghai";
const JIN10_SOURCE_ID = "jin10-flash";
const JIN10_SOURCE_NAME = "金十数据";
const JIN10_BASE_URL = "https://www.jin10.com/";
const JIN10_HOT_API = "https://3318fc142ea545eab931e22a61ec6e5c.z3c.jin10.com/flash";
const JIN10_CLASSIFY_API = "https://4a735ea38f8146198dc205d2e2d1bd28.z3c.jin10.com/classify";
const JIN10_CHANNELS = [1, 5, 9];
const JIN10_HOT_LABELS = ["爆", "沸", "热", "火"];
const JIN10_GEO_TERMS = [
  "德黑兰",
  "伊朗",
  "以色列",
  "霍尔木兹",
  "红海",
  "哈马斯",
  "胡塞",
  "袭击",
  "空袭",
  "导弹",
  "军事行动",
  "商船",
  "停火",
  "美伊",
];
const JIN10_GEO_NOISE_CATEGORIES = new Set(["A股", "美股", "港股", "政策"]);
const JIN10_HEADERS = {
  "User-Agent": "Mozilla/5.0 (compatible; NEWSHOT Jin10)",
  Accept: "application/json, text/plain, */*",
  Origin: JIN10_BASE_URL.replace(/\/$/, ""),
  Referer: JIN10_BASE_URL,
  "x-app-id": "bVBF4FyRTn5NJF5n",
  "x-version": "1.0",
};
const CLS_SOURCE_ID = "cls-telegraph";
const CLS_SOURCE_NAME = "财联社电报";
const CLS_BASE_URL = "https://www.cls.cn";
const CLS_ROLL_API = `${CLS_BASE_URL}/v1/roll/get_roll_list`;
const CLS_APP_PARAMS = {
  app: "CailianpressWeb",
  os: "web",
  sv: "8.7.9",
};
const CLS_HEADERS = {
  "User-Agent": "Mozilla/5.0 (compatible; NEWSHOT CLS Telegraph)",
  Accept: "application/json, text/plain, */*",
  Referer: `${CLS_BASE_URL}/telegraph`,
};
const MD5_SHIFTS = [
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];
const MD5_CONSTANTS = Array.from({ length: 64 }, (_, index) =>
  Math.floor(Math.abs(Math.sin(index + 1)) * 2 ** 32) >>> 0,
);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Private local control plane: never inherit public data's wildcard CORS.
    if (url.pathname.startsWith("/api/refresh/")) {
      return refreshControl(request, url);
    }

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (url.pathname === "/api/weibo/followed") {
      if (request.method !== "GET") return jsonResponse({ok: false, error: "method not allowed"}, 405);
      try {
        let payload = null;
        try { payload = env.NEWSHOT_WEIBO ? await env.NEWSHOT_WEIBO.get("weibo:followed:v1", {type: "json", cacheTtl: 60}) : null; } catch {}
        if (env.ASSETS) {
          try {
            const snapshot = await env.ASSETS.fetch(new Request(new URL("/weibo/latest.json", request.url)));
            if (snapshot.ok && (snapshot.headers.get("content-type") || "").includes("json")) {
              const asset = await snapshot.json();
              const actual = new Set((payload?.accounts || []).map(account => String(account.id)));
              const missingAccount = (asset.accounts || []).some(account => !actual.has(String(account.id)));
              if (asset.ok && Array.isArray(asset.items) && (!payload?.ok || !Array.isArray(payload.items)
                  || missingAccount || (Date.parse(asset.fetched_at) || 0) > (Date.parse(payload.fetched_at) || 0))) payload = asset;
            }
          } catch {} // Keep a valid KV snapshot if static assets are temporarily unavailable.
        }
        if (!payload?.ok || !Array.isArray(payload.items)) return jsonResponse({ok: false, error: "Weibo snapshot not yet available"}, 503);
        const fetched = Date.parse(payload.fetched_at || "");
        return jsonResponse({...payload, stale: !Number.isFinite(fetched) || Date.now() - fetched > 2 * 3600 * 1000}, 200);
      } catch {
        return jsonResponse({ok: false, error: "Weibo snapshot temporarily unavailable"}, 503);
      }
    }

    if (url.pathname === "/api/jin10/flash") {
      if (request.method !== "GET") {
        return jsonResponse({ ok: false, error: "method not allowed" }, 405);
      }
      try {
        const limit = clampNumber(url.searchParams.get("limit"), 1, 2000, 2000);
        const pages = clampNumber(url.searchParams.get("pages"), 1, 24, 24);
        const payload = await fetchJin10Flash(limit, pages);
        return jsonResponse(payload, 200);
      } catch (error) {
        return jsonResponse({ ok: false, error: error.message || String(error), generated_at: nowIso() }, 502);
      }
    }

    if (url.pathname === "/api/cls/telegraph") {
      if (request.method !== "GET") {
        return jsonResponse({ ok: false, error: "method not allowed" }, 405);
      }
      try {
        const windowStart = parseClsWindowStart(url.searchParams);
        const limit = clampNumber(url.searchParams.get("limit"), 1, 400, windowStart ? 300 : 120);
        const pages = clampNumber(url.searchParams.get("pages"), 1, 12, 8);
        const pageSize = clampNumber(url.searchParams.get("page_size"), 10, 50, windowStart ? 50 : 20);
        const payload = await fetchClsTelegraph(limit, pages, pageSize, windowStart);
        return jsonResponse(payload, 200);
      } catch (error) {
        const status = Number(error.statusCode) === 400 ? 400 : 502;
        return jsonResponse({ ok: false, error: error.message || String(error), generated_at: nowIso() }, status);
      }
    }

    return env.ASSETS.fetch(request);
  },
};

async function refreshControl(request, url) {
  const reply = (payload, status) => new Response(JSON.stringify(payload), {status, headers: {
    "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"
  }});
  if (!["127.0.0.1", "localhost"].includes(url.hostname) || url.port !== "8799") {
    return reply({ok: false, available: false, error: "此按钮需要本机更新后台；当前线上网站尚未连接后台，未发起采集。"}, 503);
  }
  const path = url.pathname === "/api/refresh/status" ? "/status" : url.pathname === "/api/refresh/start" ? "/start" : null;
  if (!path) return reply({ok: false}, 404);
  const expected = path === "/start" ? "POST" : "GET";
  if (request.method !== expected) return reply({ok: false, error: "不支持的请求方式。"}, 405);
  const origin = request.headers.get("Origin");
  if ((origin && origin !== url.origin) || (expected === "POST" && origin !== url.origin)) {
    return reply({ok: false, error: "不允许跨站更新。"}, 403);
  }
  if (expected === "POST" && (request.headers.get("Content-Type") || "").split(";")[0] !== "application/json") {
    return reply({ok: false}, 415);
  }
  try {
    const headers = {"Origin": url.origin};
    let body;
    if (expected === "POST") {
      body = await request.text();
      if (body.length > 100) return reply({ok: false}, 413);
      headers["Content-Type"] = "application/json";
      headers["X-Refresh-Nonce"] = request.headers.get("X-Refresh-Nonce") || "";
    }
    const upstream = await fetch("http://127.0.0.1:8801" + path, {method: expected, headers, body, signal: AbortSignal.timeout(10000)});
    return reply(await upstream.json(), upstream.status);
  } catch {
    return reply({ok: false, available: false, error: "本机更新后台未连接；请确认后台服务已启动。页面缓存仍可查看。"}, 503);
  }
}

async function fetchClsTelegraph(limit, pages, pageSize, windowStart) {
  const rawById = new Map();
  const errors = [];
  let lastTime = Math.floor(Date.now() / 1000) + 60;
  let pagesFetched = 0;
  let reachedWindowStart = !windowStart;
  let sourceExhausted = false;

  for (let page = 0; page < Math.max(1, pages); page += 1) {
    let rawItems;
    try {
      rawItems = await fetchClsRollPage(lastTime, pageSize);
      pagesFetched = page + 1;
    } catch (error) {
      if (!rawById.size) throw error;
      errors.push(`page ${page + 1}: ${error.message || String(error)}`);
      break;
    }
    if (!rawItems.length) {
      sourceExhausted = true;
      reachedWindowStart = true;
      break;
    }

    let nextLastTime = lastTime;
    for (const raw of rawItems) {
      const item = normalizeClsItem(raw);
      if (!item || rawById.has(item.id)) continue;
      rawById.set(item.id, item);
      nextLastTime = Math.min(nextLastTime, item.ctime);
    }
    if (nextLastTime >= lastTime) break;
    if (windowStart && nextLastTime <= windowStart) {
      reachedWindowStart = true;
      break;
    }
    if (!windowStart && rawById.size >= limit) break;
    lastTime = nextLastTime;
  }

  const eligibleItems = Array.from(rawById.values())
    .filter((item) => !windowStart || item.ctime >= windowStart)
    .sort((a, b) => b.timestamp - a.timestamp);
  const truncatedByLimit = eligibleItems.length > limit;
  const stoppedBeforeWindow = Boolean(windowStart && !reachedWindowStart && !sourceExhausted);
  const truncated = truncatedByLimit || stoppedBeforeWindow || errors.length > 0;
  const items = eligibleItems.slice(0, limit);
  const windowDate = windowStart ? new Date(windowStart * 1000) : null;

  return {
    ok: true,
    generated_at: nowIso(),
    source: CLS_SOURCE_NAME,
    source_id: CLS_SOURCE_ID,
    filter: windowStart ? `财联社电报时间流，自 ${formatChinaTime(windowDate)}` : "财联社电报时间流",
    sort: "ctime_desc",
    count: items.length,
    requested_limit: limit,
    pages_fetched: pagesFetched,
    page_size: pageSize,
    window_start: windowDate ? windowDate.toISOString() : null,
    window_start_china: windowDate ? formatChinaTime(windowDate) : null,
    window_complete: windowStart ? reachedWindowStart && !truncated : null,
    truncated,
    errors,
    items,
  };
}

function parseClsWindowStart(searchParams) {
  const since = String(searchParams.get("since") || "").trim();
  const lookback = String(searchParams.get("lookback_hours") || "").trim();
  const nowSeconds = Math.floor(Date.now() / 1000);

  if (since) {
    let timestamp;
    if (/^\d+(?:\.\d+)?$/.test(since)) {
      const numeric = Number(since);
      timestamp = numeric >= 1e12 ? Math.floor(numeric / 1000) : Math.floor(numeric);
    } else {
      const parsed = Date.parse(since);
      timestamp = Number.isFinite(parsed) ? Math.floor(parsed / 1000) : NaN;
    }
    validateClsWindowStart(timestamp, nowSeconds, "since");
    return timestamp;
  }

  if (lookback) {
    const hours = Number(lookback);
    if (!Number.isFinite(hours) || hours <= 0 || hours > 48) {
      throw clsRequestError("lookback_hours must be greater than 0 and no more than 48");
    }
    return Math.floor(nowSeconds - hours * 3600);
  }

  return null;
}

function validateClsWindowStart(timestamp, nowSeconds, field) {
  if (!Number.isFinite(timestamp)) throw clsRequestError(`${field} is not a valid timestamp or ISO date`);
  if (timestamp > nowSeconds + 300) throw clsRequestError(`${field} cannot be in the future`);
  if (timestamp < nowSeconds - 48 * 3600) throw clsRequestError(`${field} cannot be more than 48 hours ago`);
}

function clsRequestError(message) {
  const error = new Error(message);
  error.statusCode = 400;
  return error;
}

async function fetchClsRollPage(lastTime, pageSize) {
  const params = {
    ...CLS_APP_PARAMS,
    last_time: lastTime,
    refresh_type: 1,
    rn: pageSize,
  };
  params.sign = await clsSign(params);
  const query = new URLSearchParams(Object.entries(params).map(([key, value]) => [key, String(value)]));
  const payload = await fetchRemoteJson(`${CLS_ROLL_API}?${query}`, CLS_HEADERS, "cls");
  if (String(payload.errno) !== "0") {
    throw new Error(cleanText(payload.msg) || `cls errno=${payload.errno}`);
  }
  const rollData = payload.data?.roll_data;
  return Array.isArray(rollData) ? rollData.filter((item) => item && typeof item === "object") : [];
}

async function clsSign(params) {
  const payload = Object.keys(params)
    .sort((left, right) => {
      const a = left.toUpperCase();
      const b = right.toUpperCase();
      return a < b ? -1 : a > b ? 1 : 0;
    })
    .filter((key) => params[key] !== null && params[key] !== undefined)
    .map((key) => `${key}=${clsScalar(params[key])}`)
    .join("&");
  return md5Hex(await sha1Hex(payload));
}

function clsScalar(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

async function sha1Hex(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-1", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function md5Hex(value) {
  const input = new TextEncoder().encode(value);
  const paddedLength = Math.ceil((input.length + 9) / 64) * 64;
  const bytes = new Uint8Array(paddedLength);
  bytes.set(input);
  bytes[input.length] = 0x80;
  const view = new DataView(bytes.buffer);
  const bitLength = input.length * 8;
  view.setUint32(paddedLength - 8, bitLength >>> 0, true);
  view.setUint32(paddedLength - 4, Math.floor(bitLength / 2 ** 32), true);

  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;

  for (let offset = 0; offset < paddedLength; offset += 64) {
    const words = Array.from({ length: 16 }, (_, index) => view.getUint32(offset + index * 4, true));
    let a = a0;
    let b = b0;
    let c = c0;
    let d = d0;

    for (let index = 0; index < 64; index += 1) {
      let f;
      let wordIndex;
      if (index < 16) {
        f = (b & c) | (~b & d);
        wordIndex = index;
      } else if (index < 32) {
        f = (d & b) | (~d & c);
        wordIndex = (5 * index + 1) % 16;
      } else if (index < 48) {
        f = b ^ c ^ d;
        wordIndex = (3 * index + 5) % 16;
      } else {
        f = c ^ (b | ~d);
        wordIndex = (7 * index) % 16;
      }
      const rotated = rotateLeft((a + f + MD5_CONSTANTS[index] + words[wordIndex]) >>> 0, MD5_SHIFTS[index]);
      a = d;
      d = c;
      c = b;
      b = (b + rotated) >>> 0;
    }

    a0 = (a0 + a) >>> 0;
    b0 = (b0 + b) >>> 0;
    c0 = (c0 + c) >>> 0;
    d0 = (d0 + d) >>> 0;
  }

  return [a0, b0, c0, d0]
    .map((word) => [0, 8, 16, 24].map((shift) => ((word >>> shift) & 0xff).toString(16).padStart(2, "0")).join(""))
    .join("");
}

function rotateLeft(value, shift) {
  return ((value << shift) | (value >>> (32 - shift))) >>> 0;
}

function normalizeClsItem(raw) {
  const idNumber = Number(raw.id || 0);
  const ctime = Number(raw.ctime || 0);
  if (!Number.isFinite(idNumber) || idNumber <= 0 || !Number.isFinite(ctime) || ctime <= 0) return null;

  const [title, content] = splitClsTitleContent(raw);
  if (!title) return null;
  const subjects = uniqueCleanNames(raw.subjects, "subject_name", 4);
  const stocks = uniqueCleanNames(raw.stock_list, "name", 6);
  const level = cleanText(raw.level);
  const date = new Date(ctime * 1000);
  const time = formatChinaTime(date);
  const labels = [level, ...subjects, ...stocks].filter(Boolean);

  return {
    id: String(Math.trunc(idNumber)),
    title,
    content,
    summary: content,
    url: `${CLS_BASE_URL}/detail/${Math.trunc(idNumber)}`,
    source_id: CLS_SOURCE_ID,
    source_name: CLS_SOURCE_NAME,
    time,
    time_label: time.slice(11, 19),
    timestamp: date.getTime(),
    ctime: Math.trunc(ctime),
    level,
    author: cleanText(raw.author),
    subjects,
    stocks,
    labels,
    official_categories: subjects,
    rank_label: level ? `等级 ${level}` : "电报",
    metric_label: "按时间倒序",
  };
}

function splitClsTitleContent(raw) {
  let title = cleanText(raw.title);
  let content = cleanText(raw.content || raw.brief);
  if (!title) {
    const match = content.match(/^【([^】]{2,80})】(.*)$/s);
    if (match) {
      title = cleanText(match[1]);
      content = cleanText(match[2]);
    } else {
      title = content.slice(0, 72);
      content = "";
    }
  }
  const prefix = `【${title}】`;
  if (content.startsWith(prefix)) content = cleanText(content.slice(prefix.length));
  return [title, content];
}

function uniqueCleanNames(value, field, limit) {
  const names = [];
  for (const item of Array.isArray(value) ? value : []) {
    const name = cleanText(item?.[field]);
    if (name && !names.includes(name)) names.push(name);
    if (names.length >= limit) break;
  }
  return names;
}

async function fetchJin10Flash(limit, pages) {
  const [classifyMap, stream] = await Promise.all([
    fetchJin10ClassifyMap().catch(() => new Map()),
    fetchJin10NormalPages(pages, limit),
  ]);
  const rawById = new Map();
  let skippedRestricted = 0;
  for (const item of stream.items) {
    if (!isPublicJin10Record(item)) { skippedRestricted++; continue; }
    const id = String(item.id || `${item.time || ""}:${jin10DataTitle(item)}`);
    rawById.set(id, mergeJin10Raw(rawById.get(id), item));
  }

  const qualifiedItems = Array.from(rawById.values())
    .map((item) => normalizeJin10Item(item, classifyMap))
    .filter(Boolean)
    .filter(item => item.timestamp >= stream.cutoff && item.timestamp <= stream.startedAt + 300000)
    .sort((a, b) => b.timestamp - a.timestamp);
  const items = qualifiedItems.slice(0, limit);

  return {
    ok: true,
    generated_at: nowIso(),
    source: JIN10_SOURCE_NAME,
    source_id: JIN10_SOURCE_ID,
    filter: "首页公开市场快讯：普通+重要+热门；不按important或hot筛选",
    sort: "time_desc",
    count: items.length,
    items,
    public_only: true,
    window_hours: 24,
    window_complete: stream.complete && qualifiedItems.length <= limit,
    truncated: !stream.complete || qualifiedItems.length > limit,
    pages_fetched: stream.pagesFetched,
    skipped_restricted: skippedRestricted,
    errors: stream.errors,
  };
}

async function fetchJin10NormalPages(pages, limit) {
  const items = [];
  let maxTime = "";
  const startedAt = Date.now();
  const cutoff = startedAt - 24 * 3600000;
  const errors = [];
  let complete = false;
  let pagesFetched = 0;
  for (let index = 0; index < Math.max(1, pages); index += 1) {
    if (Date.now() - startedAt >= 30000) { errors.push('达到本轮请求时限，尚未确认覆盖完整窗口。'); break; }
    const params = { channel: JIN10_CHANNELS };
    if (maxTime) params.max_time = maxTime;
    let pageItems;
    try { pageItems = await fetchJin10FlashItems(params); }
    catch (error) {
      if (!items.length) throw error;
      errors.push('后续公开快讯请求失败，保留本轮已读取内容。');
      break;
    }
    pagesFetched++;
    if (!pageItems.length) { errors.push('公开接口暂无更多快讯，未确认抓全24小时；不访问受限历史。'); break; }
    items.push(...pageItems);
    const validTimes = pageItems.map(item => parseJin10Date(item.time)).filter(Boolean);
    if (validTimes.some(time => time.getTime() <= cutoff)) { complete = true; break; }
    if (items.length >= limit) { errors.push('达到单次读取数量上限，未确认抓全24小时。'); break; }
    const nextMaxTime = pageItems.map(item => String(item.time || '')).filter(Boolean).sort()[0] || '';
    if (!nextMaxTime || (maxTime && nextMaxTime >= maxTime)) { errors.push('分页游标未推进，已停止。'); break; }
    maxTime = nextMaxTime;
  }
  return {items, startedAt, cutoff, complete, pagesFetched, errors};
}

async function fetchJin10HotItems() {
  return fetchJin10FlashItems({ hot: JIN10_HOT_LABELS, channel: JIN10_CHANNELS });
}

async function fetchJin10FlashItems(params) {
  const encoded = encodeURIComponent(JSON.stringify(params));
  const payload = await fetchRemoteJson(`${JIN10_HOT_API}?params=${encoded}`, JIN10_HEADERS, "jin10");
  if (!Array.isArray(payload.data)) throw new Error('金十未返回公开快讯列表，不尝试登录或访问受限内容。');
  return payload.data.filter((item) => item && typeof item === "object");
}

async function fetchJin10ClassifyMap() {
  const payload = await fetchRemoteJson(JIN10_CLASSIFY_API, JIN10_HEADERS, "jin10");
  const categories = Array.isArray(payload.data) ? payload.data : [];
  const classifyMap = new Map();
  for (const top of categories) {
    const topId = Number(top?.id);
    const topName = cleanText(top?.name);
    if (!Number.isFinite(topId) || !topName) continue;
    classifyMap.set(topId, { name: topName, parentName: "" });
    for (const child of top.child || []) {
      const childId = Number(child?.id);
      const childName = cleanText(child?.name);
      if (Number.isFinite(childId) && childName) {
        classifyMap.set(childId, { name: childName, parentName: topName });
      }
    }
  }
  return classifyMap;
}

async function fetchRemoteJson(url, headers, source) {
  const response = await fetch(url, { headers, signal: source === 'jin10' ? AbortSignal.timeout(8000) : undefined, cf: { cacheTtl: 0, cacheEverything: false } });
  if (!response.ok) throw new Error(`${source} http ${response.status}`);
  return response.json();
}

function isPublicJin10Record(raw) {
  const data = raw?.data && typeof raw.data === 'object' ? raw.data : {};
  const restrictedKeys = ['is_vip','is_plus','vip','plus','need_login','need_pay','is_pay','pay_required','login_required','restricted','is_locked','paywall','requires_login','requires_payment','is_paid','is_charge','is_private'];
  const restricted = value => value != null && value !== false && value !== 0 && !['','0','false','no','none','null'].includes(String(value).trim().toLowerCase());
  if ([raw,data].some(node => restrictedKeys.some(key => restricted(node?.[key])))) return false;
  if ([raw,data].some(node => ['access','access_level','visibility'].some(key => !['','0','public','open','free','anonymous'].includes(String(node?.[key] || '').trim().toLowerCase())))) return false;
  const placeholder = /(?:(?:VIP|PLUS|会员|付费).{0,20}(?:专享|解锁|可见|查看)|(?:请|立即)?登录后(?:查看|阅读|解锁|可见)|解锁(?:VIP|PLUS|会员)|解锁文章|付费(?:后)?(?:查看|阅读)|开通.{0,8}(?:会员|VIP|PLUS).{0,8}(?:查看|阅读))/i;
  return ![raw.title, raw.summary, data.title, data.content].some(value => placeholder.test(cleanText(value).replace(/^[【】\s]+/,'')));
}

function normalizeJin10Item(raw, classifyMap) {
  if (!isPublicJin10Record(raw)) return null;
  const title = jin10DataTitle(raw);
  const date = parseJin10Date(raw.time);
  if (!title || !date) return null;

  const hotLabel = JIN10_HOT_LABELS.includes(cleanText(raw.hot)) ? cleanText(raw.hot) : "";
  const important = isJin10Important(raw.important);

  const labels = [];
  if (hotLabel) labels.push(hotLabel);
  if (important) labels.push("重要");

  let rankLabel = important ? "重要快讯" : "普通快讯";
  if (hotLabel && important) rankLabel = `${hotLabel} · 重要`;
  else if (hotLabel) rankLabel = `热度 ${hotLabel}`;

  const time = formatChinaTime(date);
  return {
    id: String(raw.id || `${raw.time}:${title}`),
    title,
    url: jin10ItemUrl(raw),
    source_id: JIN10_SOURCE_ID,
    source_name: JIN10_SOURCE_NAME,
    time,
    time_label: `${time.slice(11, 16)} - ${time.slice(11, 16)}`,
    timestamp: date.getTime(),
    important,
    hot: hotLabel,
    public_access: true,
    summary: cleanText(raw.data?.content || raw.summary || ''),
    labels,
    official_categories: jin10OfficialCategories(raw, classifyMap),
    rank_label: rankLabel,
    metric_label: "按时间倒序",
  };
}

function jin10OfficialCategories(raw, classifyMap) {
  let labels = [];
  for (const rawId of raw.classify || []) {
    const match = classifyMap.get(Number(rawId));
    if (!match) continue;
    const label = match.parentName || match.name;
    if (label && !labels.includes(label)) labels.push(label);
  }
  if (isJin10Geopolitical(raw)) {
    labels = [
      "地缘局势",
      ...labels.filter((label) => label !== "地缘局势" && !JIN10_GEO_NOISE_CATEGORIES.has(label)),
    ];
  }
  return labels.slice(0, 4);
}

function isJin10Geopolitical(raw) {
  const text = jin10CategoryText(raw);
  if (text.includes("地缘")) return true;
  const hits = termHits(text, JIN10_GEO_TERMS);
  const conflictHits = termHits(text, ["袭击", "空袭", "导弹", "军事行动", "商船", "停火"]);
  const actorHits = termHits(text, ["德黑兰", "伊朗", "以色列", "霍尔木兹", "红海", "哈马斯", "胡塞", "美伊"]);
  return hits.length >= 2 || (actorHits.length > 0 && conflictHits.length > 0);
}

function jin10CategoryText(raw) {
  const data = raw.data && typeof raw.data === "object" ? raw.data : {};
  return [data.title || raw.title || "", data.content || ""].map(cleanText).filter(Boolean).join(" ");
}

function jin10DataTitle(raw) {
  const data = raw.data && typeof raw.data === "object" ? raw.data : {};
  const title = cleanText(data.title || raw.title || "");
  if (title) return title;

  const content = cleanText(data.content || "");
  if (content) {
    const match = content.match(/^【([^】]{2,120})】/);
    if (match) return cleanText(match[1]);
    return content.length > 120 ? `${content.slice(0, 120).trimEnd()}...` : content;
  }

  if (raw.type === 1) {
    const subject = [data.country, data.time_period, data.name].map(cleanText).filter(Boolean).join("");
    const stats = [];
    const previous = cleanText(data.previous);
    const consensus = cleanText(data.consensus);
    const actual = cleanText(data.actual);
    if (previous) stats.push(`前值 ${previous}`);
    if (consensus) stats.push(`预期 ${consensus}`);
    if (actual) stats.push(`公布 ${actual}`);
    if (subject && stats.length) return `${subject}：${stats.join("，")}`;
    if (subject) return subject;
  }
  return "";
}

function jin10ItemUrl(raw) {
  const data = raw.data && typeof raw.data === "object" ? raw.data : {};
  const direct = cleanText(data.source_link || data.link || "");
  if (/^https?:\/\//.test(direct)) return direct;
  for (const remark of raw.remark || []) {
    const link = cleanText(remark?.link || "");
    if (/^https?:\/\//.test(link)) return link;
  }
  return JIN10_BASE_URL;
}

function mergeJin10Raw(existing, incoming) {
  if (!existing) return { ...incoming };
  const merged = { ...existing, ...incoming };
  if (isJin10Important(existing.important) || isJin10Important(incoming.important)) merged.important = 1;
  if (!merged.hot && existing.hot) merged.hot = existing.hot;
  return merged;
}

function isJin10Important(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Math.trunc(value) === 1;
  return ["1", "true", "yes"].includes(String(value ?? "").trim().toLowerCase());
}

function parseJin10Date(value) {
  const text = String(value || "").trim();
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!match) return null;
  const [, year, month, day, hour, minute, second = "00"] = match;
  return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour) - 8, Number(minute), Number(second)));
}

function formatChinaTime(date) {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: CHINA_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function cleanText(value) {
  return String(value ?? "")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function termHits(text, terms) {
  const lowered = text.toLowerCase();
  return terms.filter((term) => lowered.includes(String(term).toLowerCase()));
}

function clampNumber(value, min, max, fallback) {
  if (value == null || String(value).trim() === '') return fallback;
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(number)));
}

function nowIso() {
  return new Date().toISOString();
}

function jsonResponse(payload, status) {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-cache, no-store, must-revalidate",
      ...corsHeaders(),
    },
  });
}

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, OPTIONS",
    "access-control-allow-headers": "content-type",
  };
}
