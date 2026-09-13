"""Split big cities out of their raion into their official city districts, the way Kyiv is drawn.

For every city in the given oblasts that has city districts in OSM (KATOTTG codes with a level-5 part), this:
  - cuts the city out of the raion(s) holding it in public/data/raions.geojson and adds one polygon per district
    (OSM relations via Nominatim, simplified together so neighbours stay gap-free, clipped to the raion outline;
    small slivers left between the OSM boundary and the simplified raion outline go to the adjacent district);
  - adds the districts to data/raions.json with `city_id` = the city's KATOTTG, which the parser expands a
    whole-city mention into, and points the city itself in data/places.json at that id;
  - adds OSM neighbourhood names (place=suburb/quarter/neighbourhood) to data/aliases.json, skipping generic
    names, names shared by two cities, and names that clash with a town or a 500+ village elsewhere or an
    existing alias.
Cities that are already split (their KATOTTG is some district's city_id) are left alone, so rerunning after
enabling more oblasts only adds the new ones. Kyiv (UA80) is its own oblast and is not handled here.

Human review of the neighbourhood names lives in data/city_alias_overrides.json and is applied on every run:
"skip" lists names never to add, "add" maps names to a district or city id (overriding what the rules decided).

    uv pip install shapely          # only this script needs it
    python scripts/split_city.py UA12 UA23 UA48 UA51 UA59 UA63 UA74 [--dry-run] [--cache DIR] [--report FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

import shapely
from shapely import coverage_simplify, unary_union
from shapely.geometry import Point, mapping, shape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ingest.parser import STOP, _product, norm, stem_variants  # noqa: E402

OVERPASS = ["https://overpass.private.coffee/api/interpreter", "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
NOMINATIM = "https://nominatim.openstreetmap.org/lookup"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
CITY_OVERSHOOT = 1.1  # districts covering more than this times the city's own boundary are clipped to it
USER_AGENT = "kyiv-air-map/1.0 (github.com/zheniaslius/kyiv-air-map)"
TOLERANCE = 0.0015  # degrees; district outlines come out about as detailed as Kyiv's
GRID = 0.0001       # coordinates are stored with 4 decimals
SLIVER = 0.002      # deg² (~17 km²); smaller leftover raion pieces touching the city belong to it

# neighbourhood names that are really numbered blocks, facilities or dictionary words
GENERIC_RX = re.compile(r"\d|мікрорайон|квартал|сектор|містечко|товариств|масив|ринок|вокзал|аеропорт|завод|"
                        r"станці|урочищ|гідропарк|споруди|комбінат|[\"«»()]", re.I)
GENERIC_WORDS = {"центр", "поділ", "оболонь", "політех", "ботсад", "держпром", "студентська", "спортивна", "наукова",
                 "одеська", "підміська", "селекційна", "конторська", "добровільна", "космонавтів", "машинобудівників",
                 "стріла", "зірка", "колос", "обрій", "океан", "восход", "дружба", "перемога", "гвардія", "піонер",
                 "ластівка", "берізка", "зустріч", "супутник", "незалежне", "змичка", "лука", "балка", "левада",
                 "левади", "піски", "ставки", "липки", "горького", "скворцова", "стаханова", "бекетова", "барабашова",
                 "димитрова", "артем", "соборності", "індивідуальний", "індустріальна", "житломасив", "півколо",
                 "розвилка", "сортувальня", "нахалівка", "собачівка", "бомбей", "бам", "бмв", "ртс", "дсго", "крес"}
ADJECTIVE_RX = re.compile(r"[А-ЯІЇЄҐ][а-яіїєґ'’\-]+(ий|ій)")  # "Північний", "Зарічний": too generic alone


# ---------------------------------------------------------------- JSON files, written back in their own style
STYLES = [dict(ensure_ascii=a, indent=i, separators=sep) for a in (False, True)
          for i, sep in ((None, None), (None, (",", ":")), (0, None), (1, None), (2, None), (0, (",", ":")))]


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    obj = json.loads(text)
    for kw in STYLES:
        for nl in ("", "\n"):
            if json.dumps(obj, **kw) + nl == text:
                return obj, ("whole", kw, nl)
    if isinstance(obj, dict) and "features" in obj:  # GeoJSON with one compact feature per line
        head = text[:text.index('{"type":"Feature"')]
        for a in (False, True):
            body = ",\n".join(json.dumps(f, ensure_ascii=a, separators=(",", ":")) for f in obj["features"])
            if text.startswith(head + body) and len(text) - len(head + body) < 16:
                return obj, ("lines", head, text[len(head + body):], a)
    sys.exit(f"{path}: JSON layout does not round-trip; refusing to rewrite it")


def save(path: Path, obj, style) -> None:
    if style[0] == "whole":
        text = json.dumps(obj, **style[1]) + style[2]
    else:
        _, head, tail, a = style
        text = head + ",\n".join(json.dumps(f, ensure_ascii=a, separators=(",", ":")) for f in obj["features"]) + tail
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- network (curl: no CA-bundle surprises)
def http(url: str, *, form: str | None = None, params: dict | None = None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    cmd = ["curl", "-sf", "-m", "240", "-A", USER_AGENT, url]
    if form is not None:
        cmd += ["--data-urlencode", f"data={form}"]
    return json.loads(subprocess.run(cmd, check=True, capture_output=True).stdout)


def cached(cache: Path | None, what: str, fetch):
    path = cache / (hashlib.sha1(what.encode()).hexdigest()[:16] + ".json") if cache else None
    if path and path.exists():
        return json.loads(path.read_text())
    out = fetch()
    if path:
        path.write_text(json.dumps(out, ensure_ascii=False))
    return out


def overpass(query: str, cache: Path | None):
    def fetch():
        err = None
        for _ in range(3):
            for url in OVERPASS:
                try:
                    return http(url, form=query)
                except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
                    err = e
            time.sleep(20)
        sys.exit(f"Overpass unavailable: {err}")
    return cached(cache, query, fetch)


def city_outline(name: str, city_id: str, cache: Path | None):
    """The city's own administrative boundary (the OSM relation carrying its KATOTTG), or None."""
    params = {"q": name.replace("’", "'"), "format": "jsonv2", "countrycodes": "ua", "featureType": "city",
              "polygon_geojson": 1, "extratags": 1, "limit": 5}
    res = cached(cache, "nominatim city " + city_id, lambda: (time.sleep(1.1), http(NOMINATIM_SEARCH, params=params))[1])
    r = next((r for r in res if (r.get("extratags") or {}).get("katotth") == city_id
              and r["geojson"]["type"] in ("Polygon", "MultiPolygon")), None)
    return polygons(shape(r["geojson"]).buffer(0), 0) if r else None


# ---------------------------------------------------------------- geometry
def polygons(g, min_area: float = 2e-6):
    parts, stack = [], [g]
    while stack:
        x = stack.pop()
        if x.geom_type == "Polygon":
            if x.area > min_area:
                parts.append(x)
        elif hasattr(x, "geoms"):
            stack.extend(x.geoms)
    parts.sort(key=lambda p: -p.area)
    return parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)


def rounded(g) -> dict:
    return json.loads(json.dumps(mapping(g)), parse_float=lambda s: round(float(s), 4))


def district_name(r: dict) -> str:
    nd = r.get("namedetails") or {}
    name = (nd.get("name:uk") or r.get("name") or "").strip()
    if norm(name) == "район":
        name = (nd.get("name") or "").strip()
    name = re.sub(r"\s+район$", " район", name, flags=re.I)
    return name if norm(name).endswith("район") else name + " район"


def keys(name: str) -> list[str]:
    return [" ".join(k) for k in _product([stem_variants(w) for w in name.split()])]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("oblasts", nargs="+", help="KATOTTG oblast prefixes, e.g. UA12 UA63")
    ap.add_argument("--root", type=Path, default=ROOT, help="repo root holding data/ and public/data/ (default: this repo)")
    ap.add_argument("--cache", type=Path, help="cache OSM responses here between runs")
    ap.add_argument("--report", type=Path, help="write per-city districts and alias decisions as JSON")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)
    codes = [c.upper()[2:] for c in args.oblasts if c.upper() != "UA80"]

    files = {"geo": args.root / "public/data/raions.geojson", "raions": args.root / "data/raions.json",
             "places": args.root / "data/places.json", "aliases": args.root / "data/aliases.json"}
    (G, gs), (R, rs), (P, ps), (A, as_) = (load(files[k]) for k in ("geo", "raions", "places", "aliases"))
    done = {r["city_id"] for r in R if r.get("city_id")}

    # 1. discover districts (level-5 KATOTTG) and their cities (same code with 00 there)
    q = (f'[out:json][timeout:180];rel["boundary"="administrative"]'
         f'["katotth"~"^UA({"|".join(codes)})[0-9]{{8}}(0[1-9]|[1-9][0-9])[0-9]{{5}}$"];out tags;')
    by_city: dict[str, list[int]] = defaultdict(list)
    for e in overpass(q, args.cache)["elements"]:
        by_city[e["tags"]["katotth"][:12]].append(e["id"])
    # the city's own code is on its boundary relation for some cities and only on its place node for others
    q = f'[out:json][timeout:180];nwr["katotth"~"^({"|".join(by_city)})00[0-9]{{5}}$"];out tags;'
    cities = {}
    for e in sorted(overpass(q, args.cache)["elements"], key=lambda e: e["type"] != "relation"):
        cities.setdefault(e["tags"]["katotth"][:12], e["tags"])
    todo = {}
    for prefix, rels in sorted(by_city.items()):
        if prefix not in cities:
            print(f"skip {prefix}: no OSM element carries the city's code")
            continue
        tags = cities[prefix]
        if tags["katotth"] in done:
            print(f"skip {tags.get('name:uk') or tags.get('name')}: already split")
            continue
        todo[prefix] = (tags, sorted(rels))
    if not todo:
        print("no new cities to split; applying alias overrides only")

    # 2. district polygons, 50 per Nominatim lookup
    rel_ids = [r for _, rels in todo.values() for r in rels]
    lookup = {}
    for i in range(0, len(rel_ids), 50):
        batch = ",".join(f"R{r}" for r in rel_ids[i:i + 50])
        params = {"osm_ids": batch, "format": "jsonv2", "polygon_geojson": 1, "namedetails": 1, "extratags": 1}
        for r in cached(args.cache, "nominatim " + batch, lambda: (time.sleep(1.1), http(NOMINATIM, params=params))[1]):
            lookup[r["osm_id"]] = r

    # 3. neighbourhood points around each city (bbox of its districts; assigned by point-in-polygon below)
    boxes = []
    for _, rels in todo.values():
        w, s, e, n = unary_union([shape(lookup[r]["geojson"]) for r in rels]).bounds
        boxes.append(f'nwr["place"~"^(suburb|neighbourhood|quarter)$"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});')
    hoods = overpass(f'[out:json][timeout:180];({"".join(boxes)});out center tags;', args.cache)["elements"] if boxes else []
    overrides_path = args.root / "data/city_alias_overrides.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {}
    curated_out = {norm(n) for n in overrides.get("skip", [])}

    alias_keys = {k: name for name in A for k in keys(name)}
    report, new_aliases = {}, []
    for prefix, (tags, rels) in todo.items():
        city_id = tags["katotth"]
        city_name = tags.get("name:uk") or tags["name"]
        oblast = "UA" + prefix[2:4]
        dist = [(lookup[r]["extratags"]["katotth"], district_name(lookup[r]), polygons(shape(lookup[r]["geojson"]).buffer(0), 0))
                for r in rels]
        clean = []  # OSM districts can overlap by a hair: make them a proper coverage first
        for _, _, g in dist:
            clean.append(polygons(g.difference(unary_union(clean)) if clean else g, 0))
        # in some cities the OSM districts also cover the villages of the city's hromada (Sumy: 348 km² of districts
        # for a 106 km² city); keep only the city itself, the villages stay in their raion
        own = city_outline(city_name, city_id, args.cache)
        clipped = own is not None and unary_union(clean).area > CITY_OVERSHOOT * own.area
        if clipped:
            clean = [polygons(g.intersection(own), 0) for g in clean]
        city = unary_union(clean)

        feats = [(i, shape(f["geometry"])) for i, f in enumerate(G["features"]) if f["properties"]["oblast"] == oblast]
        hosts = sorted(((i, s) for i, s in feats if s.intersection(city).area > 0.05 * city.area),
                       key=lambda t: -t[1].intersection(city).area)
        if not hosts:
            print(f"skip {city_name}: no raion in {oblast} holds it")
            continue
        outline = unary_union([s for _, s in hosts])
        districts = [polygons(shapely.set_precision(g.intersection(outline), GRID))
                     for g in coverage_simplify(clean, TOLERANCE)]

        # cut the city out of each host raion; slivers between the two outlines go to the district they touch most
        rests = {}
        for i, s in hosts:
            keep = []
            for piece in shapely.get_parts(shapely.set_precision(s.difference(unary_union(districts)), GRID)):
                if piece.geom_type != "Polygon" or piece.area < 2e-7:
                    continue
                shared = [piece.buffer(GRID * 2).intersection(d.boundary).length for d in districts]
                k = max(range(len(districts)), key=shared.__getitem__)
                if piece.area >= SLIVER or shared[k] == 0:
                    keep.append(piece)
                else:
                    districts[k] = shapely.set_precision(unary_union([districts[k], piece]), GRID)
            rests[i] = polygons(shapely.MultiPolygon(keep))
        districts = [polygons(d) for d in districts]

        host_i = hosts[0][0]
        host = G["features"][host_i]
        host_id = host["properties"]["id"]
        for i, rest in rests.items():
            G["features"][i]["geometry"] = rounded(rest)
        place = max((p for p in P if norm(p["name"]) == norm(city_name) and p["place"] in ("city", "town")
                     and city.buffer(0.02).contains(Point(p["lon"], p["lat"]))), key=lambda p: p["pop"] or 0, default=None)
        label = place["name"] if place else city_name
        new_feats = []
        for (kid, name, _), d in zip(dist, districts):
            props = {"id": kid, "name": name, "name_en": None, "oblast": oblast, "city": label}
            new_feats.append({k: props if k == "properties" else rounded(d) if k == "geometry" else host[k] for k in host})
        G["features"][host_i + 1:host_i + 1] = new_feats
        ri = next(i for i, r in enumerate(R) if r["id"] == host_id)
        R[ri + 1:ri + 1] = [{"id": kid, "name": name, "name_en": None, "oblast": oblast, "city_id": city_id}
                            for kid, name, _ in dist]

        host_ids = {G["features"][i]["properties"]["id"] for i, _ in hosts}
        moved = []
        for p in P:
            if p is place:
                p["raion"] = city_id
            elif p["raion"] in host_ids and city.contains(Point(p["lon"], p["lat"])):
                p["raion"] = next((kid for (kid, _, _), g in zip(dist, clean) if g.contains(Point(p["lon"], p["lat"]))), p["raion"])
                moved.append(p["name"])

        for e in hoods:
            name = (e["tags"].get("name:uk") or e["tags"].get("name") or "").strip()
            c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
            if not name or c["lat"] is None:
                continue
            pt = Point(c["lon"], c["lat"])
            kid = next((kid for (kid, _, _), g in zip(dist, clean) if g.contains(pt)), None)
            if kid:
                new_aliases.append({"name": name, "district": kid, "city": label, "report": city_name})

        report[city_name] = {"city_id": city_id, "host_raions": sorted(host_ids), "places_moved": moved,
                             "place_found": bool(place), "clipped_to_city": clipped,
                             "districts": {}, "districts_ids": [kid for kid, _, _ in dist],
                             "aliases_added": {}, "aliases_dropped": {}}
        for (kid, name, raw), d in zip(dist, districts):
            report[city_name]["districts"][name] = {"id": kid, "vertices": int(shapely.get_num_coordinates(d)),
                                                    "area_kept": round(d.area / raw.area, 3), "valid": d.is_valid}
        print(f"{city_name}: {len(dist)} districts cut from {sorted(host_ids)}{' (clipped to the city boundary)' if clipped else ''};"
              f" city place {'found' if place else 'MISSING'};"
              f" moved places {moved or '-'}")

    # neighbourhood aliases: generic names out, then anything ambiguous
    names_by_district = {r["id"]: r["name"] for r in R}
    cities_by_key = defaultdict(set)
    for a in new_aliases:
        for k in keys(a["name"]):
            cities_by_key[k].add(a["city"])
    city_shapes = [unary_union([shape(f["geometry"]) for f in G["features"] if f["properties"].get("city") == c])
                   for c in {a["city"] for a in new_aliases}]
    big = defaultdict(list)
    for p in P:
        if p["place"] in ("city", "town") or (p["pop"] or 0) >= 500:
            if not any(s.contains(Point(p["lon"], p["lat"])) for s in city_shapes):
                for k in keys(p["name"]):
                    big[k].append(f"{p['name']} ({p['oblast']})")
    seen = set()
    for a in new_aliases:
        name, bucket = a["name"], report[a["report"]]
        if (name, a["district"]) in seen:
            continue
        seen.add((name, a["district"]))
        ks = keys(name)
        why = None
        if norm(name) in curated_out:
            why = "curated out"
        elif GENERIC_RX.search(name) or norm(name) in GENERIC_WORDS or norm(name) in STOP:
            why = "generic"
        elif " " not in name and ADJECTIVE_RX.fullmatch(name):
            why = "bare adjective"
        elif any(len(cities_by_key[k]) > 1 for k in ks):
            why = "name used in two cities"
        elif any(k in alias_keys and A.get(alias_keys[k]) != a["district"] for k in ks):
            why = f"existing alias {sorted({alias_keys[k] for k in ks if k in alias_keys})}"
        elif any(big[k] for k in ks):
            why = f"settlement elsewhere {sorted({x for k in ks for x in big[k]})[:4]}"
        if why:
            bucket["aliases_dropped"][name] = why
        elif name not in A:
            A[name] = a["district"]
            bucket["aliases_added"][name] = names_by_district[a["district"]]
            for k in ks:
                alias_keys[k] = name
    for c, b in report.items():
        print(f"  {c}: {len(b['aliases_added'])} aliases added, {len(b['aliases_dropped'])} dropped")
    known = {r["id"] for r in R} | {r["city_id"] for r in R if r.get("city_id")}
    for name in overrides.get("skip", []):
        if name in A and any(A[name] in b["districts_ids"] for b in report.values()):
            del A[name]
    for name, target in overrides.get("add", {}).items():
        if target not in known:
            sys.exit(f"{overrides_path.name}: {name!r} points at unknown id {target}")
        if A.get(name) != target:
            A[name] = target
            print(f"  override: {name} -> {names_by_district.get(target, target)}")

    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    if args.dry_run:
        print("dry run: nothing written")
        return
    save(files["geo"], G, gs)
    save(files["raions"], R, rs)
    save(files["places"], P, ps)
    save(files["aliases"], A, as_)
    print("written")


if __name__ == "__main__":
    main()
