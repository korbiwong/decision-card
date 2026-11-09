// 決策卡 Claude relay：Cloudflare Worker，幫網站轉發去 Anthropic。
// API key 只放喺 Cloudflare（Secret），唔會出現喺網站或者 GitHub。
// 要設兩個 Secret：
//   ANTHROPIC_API_KEY  你嘅 Anthropic API key
//   RELAY_PASS         你自己作嘅密碼；網站「設定」要打同一個
// 只接受 POST /v1/messages，同埋只畀下面嘅網站 call。

const ALLOWED_ORIGINS = [
  'https://korbiwong.github.io',
  'http://localhost:8765',
];

function cors(origin) {
  return {
    'access-control-allow-origin': origin,
    'access-control-allow-methods': 'POST, OPTIONS',
    'access-control-allow-headers': 'content-type, anthropic-version, anthropic-beta, x-relay-pass',
    'access-control-max-age': '86400',
    'vary': 'origin',
  };
}

function fail(status, type, message, origin) {
  return new Response(JSON.stringify({ type: 'error', error: { type, message } }), {
    status,
    headers: { 'content-type': 'application/json', ...cors(origin) },
  });
}

// 用固定時間比較密碼，唔好俾人逐個字估
function samePass(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('origin') || '';
    if (!ALLOWED_ORIGINS.includes(origin)) return new Response('Forbidden', { status: 403 });
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors(origin) });

    const url = new URL(request.url);
    if (request.method !== 'POST' || url.pathname !== '/v1/messages') {
      return fail(404, 'not_found_error', 'Only POST /v1/messages', origin);
    }
    if (!env.ANTHROPIC_API_KEY || !env.RELAY_PASS) {
      return fail(500, 'relay_config_error', 'Relay is missing ANTHROPIC_API_KEY or RELAY_PASS', origin);
    }
    if (!samePass(request.headers.get('x-relay-pass') || '', env.RELAY_PASS)) {
      return fail(401, 'relay_auth_error', 'Wrong relay passcode', origin);
    }

    const headers = {
      'content-type': 'application/json',
      'x-api-key': env.ANTHROPIC_API_KEY,
      'anthropic-version': request.headers.get('anthropic-version') || '2023-06-01',
    };
    const beta = request.headers.get('anthropic-beta');
    if (beta) headers['anthropic-beta'] = beta;

    const upstream = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers,
      body: request.body,
    });
    // 原封不動傳返（包括 streaming），加返 CORS
    const out = new Headers(upstream.headers);
    for (const [k, v] of Object.entries(cors(origin))) out.set(k, v);
    return new Response(upstream.body, { status: upstream.status, headers: out });
  },
};
