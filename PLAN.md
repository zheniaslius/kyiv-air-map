# Air-alert map of Ukraine by район — plan

## Status (2026-09-12)
Phases 0–5 are built and running (see README.md): corpus of 1187 messages, 193 polygons, parser at 99%
raion coverage on the corpus with 10 regression tests, SQLite store, web-preview poller, FastAPI, MapLibre UI,
deployed on Vercel (static site + one stateless Python function).
Coverage: the map draws the whole country (oblasts labelled zoomed out, raions zoomed in), but alerts are gated
to `ENABLED_OBLASTS`: `UA80,UA32` (Kyiv city + Kyiv oblast) as of 2026-09-12, widened on 2026-09-13 to add
Odesa, Sumy, Kharkiv and Chernihiv oblasts (`UA51,UA59,UA63,UA74`), with Odesa city split into its 4 districts
like Kyiv, then Dnipropetrovsk, Zaporizhzhia and Mykolaiv oblasts (`UA12,UA23,UA48`) the same day, and every big city in the
enabled oblasts split into its city districts with scripts/split_city.py; the rest is grey "coming soon".
Open: Telegram MTProto source needs your api_id/api_hash; alerts.in.ua layer deferred.
Known parser gaps: typos in place names ("Переслав"), offshore launches (Чорне море — now dropped rather than
mis-pinned, but still no raion), Moldova mentions, "той самий маршрут" follow-ups.

## Goal
A web map of Ukraine where each район lights up red when a threat (UAV, missile, etc.)
is reported there, and the red fades over time as the threat passes. Threat reports
come from a public Telegram channel, parsed automatically.

## Architecture (3 pieces)

```
Telegram public channel ──> ingest (Python, polling) ──> events store (SQLite)
                                                              │
                                        API (FastAPI): /events?since=, /raions.geojson
                                                              │
                                        frontend (MapLibre GL, single index.html)
                                        fade computed client-side every frame
```

Everything runs as one Python process plus a static page. No DB server, no queue.

## Decisions (locked 2026-09-11)
1. Channel: `@war_monitor`.
2. Granularity: район only, except the big cities of the enabled oblasts (Kyiv 10, Kharkiv 9, Dnipro 8, Zaporizhzhia 7, Kryvyi Rih 7,
   Odesa 4, Mykolaiv 4, Kamianske 3, Sumy 2, Chernihiv 2), which are split into city districts (neighbourhoods → district).
3. Fade time constants: UAV 25 min, missile 8 min, generic alert 60 min.
4. alerts.in.ua official layer: later, not in v1.

## Phase 0 — Telegram access + sample corpus (½ day)
- Get api_id/api_hash at my.telegram.org, log in once with Telethon (user session).
- Pull the last ~3–7 days of the channel via `client.iter_messages(channel, limit=N)`
  into `data/samples.jsonl`. This corpus drives parser development offline.
- Note: Telethon `events.NewMessage` does not reliably fire for large public channels
  you do not own. Plan on polling `get_messages(channel, min_id=last_seen)` every 10–15 s.
- Fallback if MTProto is a problem: scrape `https://t.me/s/<channel>` HTML (no auth,
  ~20 latest posts, works for public channels only).

## Phase 1 — Geometry + gazetteer (1 day)
- Boundaries: OSM `admin_level=6` relations inside Ukraine (post-2020 raions, 136 incl.
  Crimea) + Kyiv and Sevastopol city boundaries. Pull via Overpass or osm-boundaries.com.
  Check geoBoundaries/GADM only if OSM is painful; they may be pre-reform.
- Simplify with mapshaper to ~1–2 MB GeoJSON. Keep properties: `id`, `name_uk`,
  `oblast_uk`, `katottg` (code from the national codifier) for stable IDs.
- Gazetteer `data/gazetteer.json`: for each район, every surface form seen in text:
  nominative ("Ніжинський район"), locative ("Ніжинському районі"), abbreviations
  ("Ніжинський р-н"), bare adjective ("Ніжинщина"), plus its major towns (→ район) and
  oblast names (→ all raions in oblast, at low weight).
  Generate declension variants by rule (-ський → -ському, -ського), then fix by hand
  against the corpus.

## Phase 2 — Parser (1–2 days, offline on the corpus)
- Step 1: normalise text (lowercase, strip emoji/markdown, ё/є quirks).
- Step 2: gazetteer match → list of (raion_id, weight). Disambiguate duplicate raion
  names (e.g. several "Миколаївський") using an oblast mentioned in the same message,
  else the channel's default oblast.
- Step 3: classify threat type from keywords: `бпла|шахед|дрон|мопед` → uav,
  `ракет|крилат|балістик|кинжал` → missile, `відбій` → clear, else generic.
- Step 4: extract direction words ("курсом на", "у напрямку") — these name the *next*
  район; emit a lower-weight event for it too.
- Measure: % of messages with ≥1 raion hit; eyeball 100 random ones.
- Optional step 5: LLM fallback (Claude Haiku, structured output) only for messages
  with zero gazetteer hits. Keep it off by default; it costs money and adds latency.

## Phase 3 — Ingest + store + API (1 day)
- `events` table: `id, ts, raion_id, kind (uav|missile|generic|clear), weight,
  msg_id, channel, raw_text`.
- Poller writes new events; `відбій`/clear for a raion inserts a `clear` event that the
  client uses to zero that raion immediately.
- FastAPI: `GET /raions.geojson`, `GET /events?since=<ts>` (last 2 h by default),
  `GET /alerts/official` proxy for alerts.in.ua (cached 15 s) if Phase 0 decision 4 = yes.
- Optional `GET /stream` (SSE) later; polling every 10 s is fine for v1.

## Phase 4 — Map UI (1–2 days)
- MapLibre GL JS, one fill layer for raions, one line layer for oblast outlines,
  basemap from a free vector tile source or none (plain dark background reads well).
- Client holds `events[]`; every animation frame computes per-raion intensity
  `I = Σ w · exp(−(now − t)/τ_kind)`, clamps to [0,1], and sets `fill-opacity` via
  `setFeatureState`. Fade is therefore smooth and independent of server tick rate.
- Hover popup: район name, last 3 raw messages, minutes since last report.
- Legend, "last update" clock, and a red banner if the poller is stale > 2 min.
- Optional: time slider to scrub the last 2 h (replay from `events[]`, no extra API).

## Phase 5 — Polish + run (½ day)
- Dockerfile or a `make run`; `.env` for Telegram creds and alerts.in.ua token.
- Persist session file and SQLite in a volume.
- Deploy target TBD (a small VPS is enough; the page is static and the API is tiny).

## Repo layout
```
kyiv/
  ingest/   telegram poller + parser (python)
  api/      fastapi app
  web/      index.html, app.js, style.css
  data/     raions.geojson, gazetteer.json, samples.jsonl
  PLAN.md
```

## Risks
- Channel message format changes → parser drift. Mitigate with the sample corpus as
  a regression test (`pytest` over samples with expected raion ids).
- Duplicate район names across oblasts. Handled in Phase 2 step 2.
- Telegram account limits / bans for automated reading. Use a spare account, poll gently.
- Boundaries for occupied territory are politically sensitive; use Ukraine's official
  borders (OSM does).
