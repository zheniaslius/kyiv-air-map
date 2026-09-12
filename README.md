# Air-alert map of Ukraine by район

Live map of the whole country: each район glows red when @war_monitor reports a threat there and fades as the
threat passes. All 136 post-2020 raions are live, Kyiv is split into its 10 city districts, plus Sevastopol.

## Run

```sh
uv venv .venv -p 3.12 && uv pip install -p .venv/bin/python -r requirements-local.txt
.venv/bin/python -m server.main       # http://127.0.0.1:8000
```

Without Telegram credentials the poller scrapes the public web preview (`t.me/s/war_monitor`, ~20 latest posts,
every 10 s). For the MTProto source copy `.env.example` to `.env`, fill `TG_API_ID`/`TG_API_HASH`, export them
(`set -a; . ./.env; set +a`) and start again; the first run asks for your phone number and login code.

## Layout

| path | what |
|---|---|
| `ingest/parser.py` | message → raion events (gazetteer, declension stemming, direction roles, threat kind); `python -m ingest.parser` prints a coverage report over `data/samples.jsonl` |
| `ingest/store.py` | SQLite store (`data/events.sqlite`); `python -m ingest.store data/samples.jsonl` loads the corpus |
| `ingest/poller.py` | Telegram poller (telethon or web preview) |
| `ingest/webfeed.py` | stateless source: fetch + parse the web preview on demand (used on Vercel) |
| `server/app.py` | FastAPI routes `/api/events`, `/api/status`, `/api/config`; two backends: SQLite store or stateless |
| `server/main.py` | local server: store + poller + serves `public/` |
| `api/index.py` | Vercel serverless entrypoint (stateless backend); `vercel.json` rewrites `/api/*` here |
| `public/index.html` | MapLibre map; fade computed client-side every second |
| `public/data/raions.geojson` | 136 post-2020 raions + 10 Kyiv city districts + Sevastopol (OSM, simplified) |
| `public/data/oblasts.geojson` | 27 oblast outlines with `name`, drawn as the thicker borders |
| `public/data/oblast-labels.geojson` | one label anchor per oblast, shown below zoom 6.2; regenerate with `python scripts/oblast_label_points.py` |
| `data/places.json` | 29k settlements → raion via KATOTTG code (OSM) |
| `data/aliases.json` | name → raion overrides; ~680 Kyiv neighbourhoods → city district, generated from OSM points |

## Coverage

The whole country is live: every raion in `public/data/raions.geojson` (136 raions, Kyiv's 10 city districts,
Sevastopol) reacts to events, and the API returns events for all of them — there is no per-region gate any more.
Kyiv keeps its finer granularity: neighbourhood names in `data/aliases.json` resolve to a city district, and a
"Київ:" message with no recognisable neighbourhood lights all 10 districts at half weight.

The map labels oblasts below zoom 6.2 and raions above it; the "назви" checkbox toggles both.

## Deploy (Vercel)

The site runs on Vercel as static files plus one Python function. There is no background poller there: each
`/api/events` call fetches the channel's public web preview (up to 4 pages, ~80 posts, 3 h window), parses it,
and is cached at the CDN for 10 s so all visitors share one fetch. Pushes to `main` deploy automatically via the
Vercel GitHub integration. Manual: `vercel deploy --prod`.

## Tuning

Fade constants live in `TAU_MIN` in `ingest/parser.py` (minutes): jet 15, uav 25, cruise 10, ballistic 8, bomb 8.
Role weights in `ROLE_WEIGHT`: current 1.0, target 0.6, origin 0.3, whole-oblast mention 0.25, whole-Kyiv mention 0.5
(a "Київ:" message whose place is not a known neighbourhood lights all 10 city districts at half weight).
Intensity per raion = Σ weight × count-factor × exp(−age/τ), clamped to 1. A "чисто / відбій" message zeroes the raion
and flashes it green for 10 min.

Threat kind comes from keywords first (реактив, Бандероль, баліст…), then from the channel's own line markers:
🅿️ / 🔄 jet drone, ⚠️ regular drone, ‼️ ☄ 🟣 ballistic, 💣 glide bomb. The UI strips these markers from displayed text.

After changing the parser: `python -c "from ingest.store import Store; print(Store().reparse_all())"`.

Tests: `.venv/bin/python -m pytest tests`.
