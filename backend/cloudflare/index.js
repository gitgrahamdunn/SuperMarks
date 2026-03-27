const D1_BRIDGE_PREFIX = "/_supermarks/d1";
const D1_BRIDGE_HEADER = "x-supermarks-bridge-token";

function jsonResponse(payload, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("content-type", "application/json");
  return new Response(JSON.stringify(payload), { ...init, headers });
}

function bridgeToken(env) {
  return String(env.SUPERMARKS_D1_BRIDGE_TOKEN || env.BACKEND_API_KEY || "").trim();
}

function ensureBridgeAuthorized(request, env) {
  const expected = bridgeToken(env);
  const presented = String(request.headers.get(D1_BRIDGE_HEADER) || "").trim();
  return Boolean(expected) && presented === expected;
}

async function parseJsonBody(request) {
  try {
    return await request.json();
  } catch {
    return {};
  }
}

function requireD1Binding(env) {
  if (!env.SUPERMARKS_DB) {
    throw new Error("SUPERMARKS_DB binding is not configured");
  }
  return env.SUPERMARKS_DB;
}

function bindParams(statement, params) {
  return Array.isArray(params) && params.length > 0 ? statement.bind(...params) : statement;
}

async function handleD1Bridge(request, env) {
  if (!ensureBridgeAuthorized(request, env)) {
    return jsonResponse({ detail: "Unauthorized D1 bridge request" }, { status: 401 });
  }

  let db;
  try {
    db = requireD1Binding(env);
  } catch (error) {
    return jsonResponse({ detail: String(error.message || error) }, { status: 503 });
  }

  const url = new URL(request.url);
  const path = url.pathname.slice(D1_BRIDGE_PREFIX.length) || "/";
  const payload = await parseJsonBody(request);

  try {
    if (path === "/health") {
      const result = await db.prepare("SELECT 1 AS ok").first();
      return jsonResponse({ ok: true, result });
    }

    if (path === "/query") {
      const sql = String(payload.sql || "").trim();
      if (!sql) return jsonResponse({ detail: "sql is required" }, { status: 400 });
      const stmt = bindParams(db.prepare(sql), payload.params);
      const resultMode = String(payload.result_mode || "all");
      if (resultMode === "first") {
        const row = await stmt.first();
        return jsonResponse({ row });
      }
      const result = await stmt.all();
      return jsonResponse({ rows: result.results || [], meta: result.meta || {} });
    }

    if (path === "/run") {
      const sql = String(payload.sql || "").trim();
      if (!sql) return jsonResponse({ detail: "sql is required" }, { status: 400 });
      const stmt = bindParams(db.prepare(sql), payload.params);
      const result = await stmt.run();
      return jsonResponse(result);
    }

    if (path === "/batch") {
      const statements = Array.isArray(payload.statements) ? payload.statements : [];
      if (statements.length === 0) return jsonResponse({ detail: "statements are required" }, { status: 400 });
      const prepared = statements.map((entry) => bindParams(db.prepare(String(entry.sql || "")), entry.params));
      const results = await db.batch(prepared);
      return jsonResponse({ results });
    }

    if (path === "/exec") {
      const sql = String(payload.sql || "").trim();
      if (!sql) return jsonResponse({ detail: "sql is required" }, { status: 400 });
      const result = await db.exec(sql);
      return jsonResponse(result);
    }

    return jsonResponse({ detail: "Unknown D1 bridge path" }, { status: 404 });
  } catch (error) {
    return jsonResponse(
      { detail: String(error.message || error) },
      { status: 500 },
    );
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith(D1_BRIDGE_PREFIX)) {
      return handleD1Bridge(request, env);
    }
    return jsonResponse({ detail: "Unknown path" }, { status: 404 });
  },
};
