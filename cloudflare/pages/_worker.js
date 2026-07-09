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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (url.pathname === "/api/jin10/flash") {
      if (request.method !== "GET") {
        return jsonResponse({ ok: false, error: "method not allowed" }, 405);
      }
      try {
        const limit = clampNumber(url.searchParams.get("limit"), 1, 120, 80);
        const payload = await fetchJin10Flash(limit);
        return jsonResponse(payload, 200);
      } catch (error) {
        return jsonResponse({ ok: false, error: error.message || String(error), generated_at: nowIso() }, 502);
      }
    }

    return env.ASSETS.fetch(request);
  },
};

async function fetchJin10Flash(limit) {
  const [classifyMap, normalItems, hotItems] = await Promise.all([
    fetchJin10ClassifyMap(),
    fetchJin10NormalPages(4),
    fetchJin10HotItems(),
  ]);

  const rawById = new Map();
  for (const item of normalItems) {
    if (!isJin10Important(item.important)) continue;
    const id = String(item.id || `${item.time || ""}:${jin10DataTitle(item)}`);
    rawById.set(id, mergeJin10Raw(rawById.get(id), item));
  }
  for (const item of hotItems) {
    const id = String(item.id || `${item.time || ""}:${jin10DataTitle(item)}`);
    rawById.set(id, mergeJin10Raw(rawById.get(id), item));
  }

  const items = Array.from(rawById.values())
    .map((item) => normalizeJin10Item(item, classifyMap))
    .filter(Boolean)
    .sort((a, b) => b.timestamp - a.timestamp)
    .slice(0, limit);

  return {
    ok: true,
    generated_at: nowIso(),
    source: JIN10_SOURCE_NAME,
    source_id: JIN10_SOURCE_ID,
    filter: "important=1 或 爆/沸/热/火",
    sort: "time_desc",
    count: items.length,
    items,
  };
}

async function fetchJin10NormalPages(pages) {
  const items = [];
  let maxTime = "";
  for (let index = 0; index < Math.max(1, pages); index += 1) {
    const params = { channel: JIN10_CHANNELS };
    if (maxTime) params.max_time = maxTime;
    const pageItems = await fetchJin10FlashItems(params);
    if (!pageItems.length) break;
    items.push(...pageItems);
    const nextMaxTime = String(pageItems[pageItems.length - 1]?.time || "");
    if (!nextMaxTime || nextMaxTime === maxTime) break;
    maxTime = nextMaxTime;
  }
  return items;
}

async function fetchJin10HotItems() {
  return fetchJin10FlashItems({ hot: JIN10_HOT_LABELS, channel: JIN10_CHANNELS });
}

async function fetchJin10FlashItems(params) {
  const encoded = encodeURIComponent(JSON.stringify(params));
  const payload = await fetchJson(`${JIN10_HOT_API}?params=${encoded}`, JIN10_HEADERS);
  return Array.isArray(payload.data) ? payload.data.filter((item) => item && typeof item === "object") : [];
}

async function fetchJin10ClassifyMap() {
  const payload = await fetchJson(JIN10_CLASSIFY_API, JIN10_HEADERS);
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

async function fetchJson(url, headers) {
  const response = await fetch(url, { headers, cf: { cacheTtl: 0, cacheEverything: false } });
  if (!response.ok) throw new Error(`jin10 http ${response.status}`);
  return response.json();
}

function normalizeJin10Item(raw, classifyMap) {
  const title = jin10DataTitle(raw);
  const date = parseJin10Date(raw.time);
  if (!title || !date) return null;

  const hotLabel = JIN10_HOT_LABELS.includes(cleanText(raw.hot)) ? cleanText(raw.hot) : "";
  const important = isJin10Important(raw.important);
  if (!important && !hotLabel) return null;

  const labels = [];
  if (hotLabel) labels.push(hotLabel);
  if (important) labels.push("重要");

  let rankLabel = "重要快讯";
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
