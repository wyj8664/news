const QUOTES_KEY = "markets:quotes";
const CHINA_TZ = "Asia/Shanghai";

const DEFAULT_WATCHLIST = [
  {
    id: "pingan_bank",
    name: "平安银行",
    symbol: "sz000001",
    type: "tencent_stock",
    market: "深交所",
    unit: "元",
    url: "https://gu.qq.com/sz000001/gp",
  },
  {
    id: "gold_spot",
    name: "现货黄金",
    symbol: "hf_XAU",
    type: "tencent_global",
    market: "贵金属",
    unit: "美元/盎司",
    url: "https://gu.qq.com/hf/hf_XAU",
  },
];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(env) });
    }

    if (url.pathname === "/markets/quotes.json" || url.pathname === "/api/markets/quotes.json") {
      let payload = await readQuotes(env);
      if (!payload || url.searchParams.get("refresh") === "1") {
        payload = await refreshQuotes(env);
      }
      return jsonResponse(payload, env);
    }

    if (url.pathname === "/api/health") {
      return jsonResponse({ ok: true, generated_at: nowIso() }, env);
    }

    return new Response("Not found", { status: 404, headers: corsHeaders(env) });
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(refreshQuotes(env));
  },
};

async function readQuotes(env) {
  const raw = await env.NEWSHOT_KV.get(QUOTES_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function refreshQuotes(env) {
  const previous = await readQuotes(env);
  const previousItems = new Map((previous?.items || []).map((item) => [item.id, item]));
  const errors = [];
  const items = [];

  for (const entry of watchlist(env)) {
    try {
      const item = await fetchItem(entry);
      items.push(attachHistory(item, previousItems.get(item.id)));
    } catch (error) {
      const stale = previousItems.get(entry.id);
      if (stale) {
        items.push({ ...stale, stale: true });
      }
      errors.push(`${entry.name || entry.symbol}: ${error.message || String(error)}`);
    }
  }

  const payload = {
    generated_at: nowIso(),
    refresh_seconds: Number(env.REFRESH_SECONDS || 600),
    source: "腾讯行情公开接口，仅作行情观察",
    items,
    errors,
  };

  await env.NEWSHOT_KV.put(QUOTES_KEY, JSON.stringify(payload), {
    metadata: { generated_at: payload.generated_at },
  });

  return payload;
}

function watchlist(env) {
  if (!env.WATCHLIST_JSON) return DEFAULT_WATCHLIST;
  try {
    const parsed = JSON.parse(env.WATCHLIST_JSON);
    return Array.isArray(parsed) && parsed.length ? parsed : DEFAULT_WATCHLIST;
  } catch {
    return DEFAULT_WATCHLIST;
  }
}

async function fetchItem(entry) {
  if (entry.type === "tencent_stock") return parseStock(entry, await fetchTencent(entry.symbol));
  if (entry.type === "tencent_global") return parseGlobal(entry, await fetchTencent(entry.symbol));
  throw new Error(`unsupported quote type: ${entry.type}`);
}

async function fetchTencent(symbol) {
  const response = await fetch(`https://qt.gtimg.cn/q=${encodeURIComponent(symbol)}`, {
    headers: {
      "User-Agent": "Mozilla/5.0",
      Referer: "https://finance.qq.com/",
    },
  });
  if (!response.ok) throw new Error(`quote http ${response.status}`);

  const buffer = await response.arrayBuffer();
  let text;
  try {
    text = new TextDecoder("gbk").decode(buffer);
  } catch {
    text = new TextDecoder().decode(buffer);
  }

  if (text.includes("pv_none_match")) throw new Error(`empty quote for ${symbol}`);
  const match = text.trim().match(/v_[^=]+="(.*)";?$/);
  if (!match) throw new Error(`unexpected quote format for ${symbol}`);
  return match[1];
}

function parseStock(entry, raw) {
  const fields = raw.split("~");
  if (fields.length < 35) throw new Error(`unexpected stock fields for ${entry.symbol}`);

  const price = numberOrNull(fields[3]);
  const prevClose = numberOrNull(fields[4]);
  let change = numberOrNull(fields[31]);
  let percent = numberOrNull(fields[32]);

  if (change === null && price !== null && prevClose !== null) change = price - prevClose;
  if (percent === null && change !== null && prevClose) percent = (change / prevClose) * 100;

  return {
    id: entry.id,
    name: entry.name || fields[1],
    symbol: entry.symbol,
    market: entry.market || "",
    type: "stock",
    unit: entry.unit || "",
    url: entry.url || "",
    price,
    change,
    percent,
    open: numberOrNull(fields[5]),
    prev_close: prevClose,
    high: numberOrNull(fields[33]),
    low: numberOrNull(fields[34]),
    volume: numberOrNull(fields[36]),
    turnover: numberOrNull(fields[37]),
    time: quoteTime(fields[30]),
    source: "腾讯行情",
  };
}

function parseGlobal(entry, raw) {
  const fields = raw.split(",");
  if (fields.length < 14) throw new Error(`unexpected global fields for ${entry.symbol}`);

  const price = numberOrNull(fields[0]);
  const prevClose = numberOrNull(fields[7]);
  const change = price !== null && prevClose !== null ? price - prevClose : null;

  return {
    id: entry.id,
    name: entry.name || fields[13]?.trim() || entry.symbol,
    symbol: entry.symbol,
    market: entry.market || "",
    type: "commodity",
    unit: entry.unit || "",
    url: entry.url || "",
    price,
    change,
    percent: numberOrNull(fields[1]),
    open: numberOrNull(fields[8]),
    prev_close: prevClose,
    high: numberOrNull(fields[4]),
    low: numberOrNull(fields[5]),
    time: quoteTime(`${fields[12]} ${fields[6]}`),
    source: "腾讯行情",
  };
}

function attachHistory(item, previous) {
  const history = Array.isArray(previous?.history) ? previous.history.slice() : [];
  if (Number.isFinite(item.price)) {
    const point = { time: item.time || nowText(), price: item.price };
    const last = history[history.length - 1];
    if (!last || last.time !== point.time || last.price !== point.price) {
      history.push(point);
    }
  }
  return { ...item, history: history.slice(-80) };
}

function numberOrNull(value) {
  const number = Number(String(value ?? "").trim());
  return Number.isFinite(number) ? number : null;
}

function quoteTime(value) {
  const text = String(value || "").trim();
  if (/^\d{14}$/.test(text)) {
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)} ${text.slice(8, 10)}:${text.slice(10, 12)}:${text.slice(12, 14)}`;
  }
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)) return text;
  return text || nowText();
}

function nowIso() {
  return new Date().toISOString();
}

function nowText() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: CHINA_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function jsonResponse(payload, env) {
  return new Response(JSON.stringify(payload, null, 2), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-cache, no-store, must-revalidate",
      ...corsHeaders(env),
    },
  });
}

function corsHeaders(env) {
  return {
    "access-control-allow-origin": env.CORS_ORIGIN || "*",
    "access-control-allow-methods": "GET, OPTIONS",
    "access-control-allow-headers": "content-type",
  };
}
