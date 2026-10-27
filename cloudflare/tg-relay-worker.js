/**
 * Telegram WebSocket Relay Worker — userbot_own v3.2.7 "cf" mode
 * ============================================================
 *
 * Purpose
 * -------
 * A minimal Cloudflare Worker that relays raw WebSocket bytes between
 * the userbot (running behind a network that blocks Telegram IPs) and
 * Telegram's official WebSocket MTProto endpoints:
 *
 *     wss://<this-worker>.workers.dev/<dc-host>/apiws
 *         →  wss://<dc-host>/apiws   (e.g. venus-1.web.telegram.org/apiws)
 *
 * Why this works where a direct connection fails
 * ----------------------------------------------
 * On the filtered network, DNS for *.web.telegram.org resolves to raw
 * Telegram IPs (e.g. 149.154.167.99), which the ISP blocks. But
 * *.workers.dev resolves to real Cloudflare edge IPs, which are
 * reachable. The Worker's *outbound* connection to Telegram runs from
 * Cloudflare's own network — unaffected by the user's ISP filtering.
 *
 * Security
 * --------
 * The relay only ever dials destinations matching
 * /^[a-z]+(-1)?\.web\.telegram\.org$/ — Telegram's official web
 * endpoints. No other destination is possible, so this worker can
 * never be abused as an open proxy. (This is also the hard-won lesson
 * of the public CF-Workers-TGProxy project: pointing workers at
 * arbitrary domains produces edge errors 1034/403.)
 *
 * Deploy (dash.cloudflare.com, free plan is fine — 100k req/day):
 *   1. Workers & Pages → Create → Worker → name it (e.g. "userbot-relay")
 *   2. "Edit code" → paste this entire file → Deploy
 *   3. Note the URL: https://userbot-relay.<your-subdomain>.workers.dev
 *   4. In the userbot: run.bat → Settings → Connection mode →
 *      "Cloudflare (CF Proxy)" → paste the domain
 *      (userbot-relay.<your-subdomain>.workers.dev)
 *
 * The bot then dials:
 *   wss://userbot-relay.<sub>.workers.dev/venus-1.web.telegram.org/apiws
 * and the Worker relays both directions, byte-for-byte.
 */

// Only official Telegram web endpoints. The "-1" variants raise the
// simultaneous-request limit per hostname (per official MTProto docs).
const ALLOWED_HOST = /^[a-z]+(-1)?\.web\.telegram\.org$/;
const ALLOWED_HOST_PORT = /^([a-z]+)(-1)?\.web\.telegram\.org:(\d+)$/;

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Path: /<dc-host>[:port]/apiws
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length !== 2 || parts[1].toLowerCase() !== "apiws") {
      return new Response("not found", { status: 404 });
    }

    // Validate + normalize the destination BEFORE dialing.
    let destHost = parts[0].toLowerCase();
    let destPort = 443;
    const pm = destHost.match(ALLOWED_HOST_PORT);
    if (pm) {
      destPort = parseInt(pm[3], 10);
      destHost = `${pm[1]}${pm[2] ?? ""}.web.telegram.org`;
    } else if (!ALLOWED_HOST.test(destHost)) {
      return new Response("forbidden destination", { status: 403 });
    }

    // Non-WebSocket clients (e.g. someone opening the URL in a browser)
    // get a clean 400 here instead of attempting an upstream dial —
    // this is also the documented "is the worker alive" smoke test.
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      return new Response("expected websocket upgrade", { status: 400 });
    }

    // Outbound: dial Telegram's WebSocket endpoint from CF's edge.
    // In Workers, fetch() with an Upgrade:websocket header returns a
    // 101 response whose `webSocket` property is the server-side socket.
    const upstream = new Request(`https://${destHost}:${destPort}/apiws`, {
      headers: {
        "Upgrade": "websocket",
        "Sec-WebSocket-Protocol": "binary",
      },
    });
    let resp;
    try {
      resp = await fetch(upstream);
    } catch (e) {
      return new Response("upstream dial failed: " + e, { status: 502 });
    }
    if (resp.status !== 101 || !resp.webSocket) {
      return new Response("upstream not websocket: " + resp.status, { status: 502 });
    }
    const tg = resp.webSocket;

    // Inbound: create the pair CF hands to the client.
    // WebSocketPair() returns an ordinary object {0: client, 1: server};
    // it is NOT iterable in the Workers runtime, so array destructuring
    // throws (the "error 1101" seen when opening the URL in a browser).
    // Object.values() is the working form (owner-verified on the live
    // worker 2026-10-25).
    const [client, server] = Object.values(new WebSocketPair());

    // BOTH ends must be accept()-ed before the runtime dispatches any
    // message events on them. For the server end that's well known;
    // for the outbound fetch()-returned socket it is equally required
    // (Workers docs, "Write a WebSocket client"): without tg.accept()
    // the socket silently drops all traffic — the exact failure seen
    // when live-testing the first deployed version (handshake 101 OK,
    // then zero bytes relayed in either direction).
    // allowHalfOpen lets each side's close be coordinated with the
    // other side manually (the documented WebSocket-proxying pattern)
    // instead of the runtime auto-closing one side behind our back.
    server.accept({ allowHalfOpen: true });
    tg.accept({ allowHalfOpen: true });

    // Close handlers receive (code, reason, wasClean) as plain arguments
    // in Workers — not an event object. Extract defensively either way.
    const closeArgs = (a, b) =>
      typeof a === "number" ? [a, b] : [a?.code, a?.reason];

    // websocket_standard_binary_type (compat date ≥ 2026-03-17) delivers
    // message events as Blobs. Forwarding a Blob as-is can emit a TEXT
    // WebSocket frame; Telegram's endpoint then sees invalid-UTF-8
    // garbage and closes with 1006 — the exact "handshake OK, then
    // silence" failure seen live-testing on 2026-10-26. Normalizing
    // every frame to an ArrayBuffer forces binary frames both ways
    // (verified end-to-end: Telethon obfuscated2 over this relay).
    const toBuf = (d) =>
      d instanceof Blob ? d.arrayBuffer() : Promise.resolve(d);

    // Pump bytes both directions, transparently. WebSocket messages
    // here are one duplex byte stream — MTProto's own transport
    // framing manages chunking; we relay frames as-is.
    server.addEventListener("message", (ev) => {
      toBuf(ev.data).then((b) => {
        try { tg.send(b); } catch (e) {}
      });
    });
    server.addEventListener("close", (a, b) => {
      const [code, reason] = closeArgs(a, b);
      try { tg.close(code, reason); } catch (e) {}
      // allowHalfOpen disables the auto-reply, so complete THIS
      // side's close handshake too (silently ignored if already done).
      try { server.close(code, reason); } catch (e) {}
    });

    tg.addEventListener("message", (ev) => {
      toBuf(ev.data).then((b) => {
        try { server.send(b); } catch (e) {}
      });
    });
    tg.addEventListener("close", (a, b) => {
      const [code, reason] = closeArgs(a, b);
      try { server.close(code, reason); } catch (e) {}
      try { tg.close(code, reason); } catch (e) {}
    });

    return new Response(null, {
      status: 101,
      webSocket: client,
    });
  },
};
