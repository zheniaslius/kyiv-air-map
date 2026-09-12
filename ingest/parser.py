"""Parse @war_monitor messages into raion-level threat events.

Usage (report on the sample corpus):
    python -m ingest.parser data/samples.jsonl
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

DATA = Path(__file__).resolve().parent.parent / "data"
KYIV = "UA80000000000093317"  # aggregate id: expands to all Kyiv city districts (oblast UA80)

# ---------------------------------------------------------------- config
# Fade time constants in minutes, per threat kind. Tune freely.
TAU_MIN = {
    "jet": 15,        # реактивний БпЛА (fast)
    "uav": 25,        # Shahed-type / generic БпЛА
    "cruise": 10,     # крилаті ракети, мгКР "Бандероль"
    "ballistic": 8,   # Іскандер etc.
    "bomb": 8,        # КАБ / УМПБ
}
ROLE_WEIGHT = {"current": 1.0, "target": 0.6, "origin": 0.3, "area": 0.25, "city": 0.5}
# "city": whole-Kyiv mention with no district resolved -> every Kyiv district at half weight

KIND_RX = [
    ("jet", r"реактив|сікер"),
    ("cruise", r"мгкр|бандерол|крилат|\bкр\b|калібр|х-101|х-59|х-31"),
    ("ballistic", r"баліст|іскандер|кинджал|циркон"),
    ("bomb", r"умпб|каб\b|авіабомб"),
    ("uav", r"бпла|шахед|герань|дрон|мопед"),
]
# @war_monitor prefixes each line with a marker; used only when no keyword says otherwise
EMOJI_KIND = {"🅿": "jet", "🔄": "jet", "⚠": "uav", "‼": "ballistic", "☄": "ballistic", "🟣": "ballistic", "💣": "bomb"}
CLEAR_RX = re.compile(r"дорозвідка до відбою|\bчисто\b|відбій")
IGNORE_RX = re.compile(
    r"^📡|#зведення|#обстановка|загальна оцінка загроз|загроза балістики з|ракетна небезпека з|"
    r"^дорозвідка по|вихід бортів|входження крилатих|стратегічна авіація"
)
ORIGIN_KW = {"від", "з", "із", "зі"}
TARGET_KW = {"напрямку", "на", "курсом", "вектор", "→", "->", "курс"}
CURRENT_KW = {"повз", "над", "через", "далі", "у", "в", "по", "біля", "поблизу", "район", "районі"}
COUNT_RX = re.compile(r"(\d+)\s*[хx×]")
TOKEN_RX = re.compile(r"[А-ЯІЇЄҐа-яіїєґ'\-]+|→|->|/")
APOS = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "‘": "'"})

# oblast code -> stems that identify it in free text (matched as whole-word prefixes)
OBLAST_STEMS = {
    "UA05": ["вінниччин"], "UA07": ["волин"], "UA12": ["дніпропетровщин"], "UA14": ["донеччин"],
    "UA18": ["житомирщин"], "UA21": ["закарпатт"], "UA23": ["запоріжж"], "UA26": ["прикарпатт", "франківщин"],
    "UA32": ["київщин"], "UA35": ["кіровоградщин"], "UA44": ["луганщин"], "UA46": ["львівщин"],
    "UA48": ["миколаївщин"], "UA51": ["одещин"], "UA53": ["полтавщин"], "UA56": ["рівненщин"],
    "UA59": ["сумщин"], "UA61": ["тернопільщин"], "UA63": ["харківщин"], "UA65": ["херсонщин"],
    "UA68": ["хмельниччин"], "UA71": ["черкащин"], "UA73": ["буковин", "чернівеччин"], "UA74": ["чернігівщин"],
    "UA01": ["крим"],
}
OBLAST_ADJ = {  # "<adj>ська область"
    "вінницьк": "UA05", "волинськ": "UA07", "дніпропетровськ": "UA12", "донецьк": "UA14", "житомирськ": "UA18",
    "закарпатськ": "UA21", "запорізьк": "UA23", "івано-франківськ": "UA26", "київськ": "UA32", "кіровоградськ": "UA35",
    "луганськ": "UA44", "львівськ": "UA46", "миколаївськ": "UA48", "одеськ": "UA51", "полтавськ": "UA53",
    "рівненськ": "UA56", "сумськ": "UA59", "тернопільськ": "UA61", "харківськ": "UA63", "херсонськ": "UA65",
    "хмельницьк": "UA68", "черкаськ": "UA71", "чернівецьк": "UA73", "чернігівськ": "UA74",
}
# "<adj> водосховище / море / ГЕС" is a named feature, not a settlement: on its own the adjective stems to a
# city ("Київським" -> Київ) and would light it up. Known features map to the raion that holds them; the rest
# are dropped rather than guessed at.
FEATURE_NOUNS = ("водосховищ", "мор", "лиман", "затоц", "заток", "гес", "шосе")
VYSHHOROD = "UA32100000000065867"
FEATURES = {
    ("київськ", "водосховищ"): VYSHHOROD,  # Київське водосховище — north of the city, in Вишгородський район
    ("київськ", "мор"): VYSHHOROD,         # the channel also calls it "Київське море"
    ("київськ", "гес"): VYSHHOROD,
}
KYIV_CITY_DISTRICTS = ["голосіївськ", "дарницьк", "деснянськ", "дніпровськ", "оболонськ", "печерськ",
                       "подільськ", "святошинськ", "солом'янськ", "шевченківськ"]
# capitalised common words that collide with village names
STOP = {"увага", "загально", "загальна", "друга", "перша", "нові", "нова", "нове", "далі", "рух", "це", "декілька",
        "група", "групи", "місто", "міста", "область", "сектор", "вектор", "вихід", "ціль", "цілі", "спуск",
        "пуски", "пуск", "робота", "робить", "коло", "межі", "зараз", "також", "ще", "та", "і", "й", "у", "в", "по",
        "на", "з", "до", "від", "над", "повз", "через", "для", "або", "не", "ні", "так", "все", "всі", "весь",
        "дорозвідка", "відбій", "чисто", "реактив", "реактиви", "бпла", "бандероль", "бандеролі", "сікер", "сікера", "сікери",
        "герань", "вибухи", "зв", "кр", "умпб", "умпб-", "каб", "ачм", "загроза", "мінус", "калібр", "мадяр", "сбс", "рлс", "ае",
        "бп", "центр", "нова", "забудова", "рембаза", "лівобережний", "мінський", "міський", "масив", "йм", "очікуємо", "проведено"}

ENDINGS = ["ського", "ському", "ським", "ською", "ської", "ського", "ими", "ими", "ому", "ого", "ої", "ою", "ем", "ом", "ів",
           "ам", "ах", "ями", "ям", "ій", "ий", "ім", "их", "а", "у", "и", "і", "е", "о", "я", "ю", "ь", "є"]


def norm(s: str) -> str:
    return s.translate(APOS).lower().replace("ё", "е")


def stem(word: str) -> str:
    w = norm(word)
    for e in ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 3:
            return w[: -len(e)]
    return w


def stem_variants(word: str) -> set[str]:
    """Stem plus і→о/е/є alternation in the last syllable (Київ→Києва, Ріг→Рогу, Ірпінь→Ірпеня)."""
    s = stem(word)
    out = {s}
    i = max(s.rfind("і"), s.rfind("ї"))
    if i > 0 and not re.search(r"[аеиоуіїяюєь]", s[i + 1:]):
        for v in "оеє":
            out.add(s[:i] + v + s[i + 1:])
    return out


def adj_stem(word: str) -> str:
    """Stem for adjectival raion names: Броварському/Броварського/Броварський -> броварськ."""
    return re.sub(r"(ий|ого|ому|им|ім|а|ої|ій|ою)$", "", norm(word))


def feature_adj(word: str) -> str:
    """Any case of a -ський adjective -> its stem: Київським/Київського/Київське -> київськ."""
    return re.sub(r"(ськ)\w*$", r"\1", norm(word))


RANK = {"city": 4, "town": 3, "village": 2, "hamlet": 1, "urban": 2}


class Gazetteer:
    def __init__(self):
        self.raions = {r["id"]: r for r in json.load(open(DATA / "raions.json"))}
        self.by_oblast: dict[str, list[str]] = defaultdict(list)
        for r in self.raions.values():
            self.by_oblast[r["oblast"]].append(r["id"])
        # raion adjective stem -> id  ("Броварський район" -> "броварськ")
        self.raion_adj: dict[str, list[str]] = defaultdict(list)
        for r in self.raions.values():
            m = re.match(r"(.+?)(ий|а)\s+район", norm(r["name"]))
            if m:
                self.raion_adj[m.group(1)].append(r["id"])
        # settlement stem -> [(rank, pop, raion_id, name)]
        self.places: dict[str, list[tuple]] = defaultdict(list)
        for p in json.load(open(DATA / "places.json")):
            words = p["name"].split()
            keys = [" ".join(k) for k in _product([stem_variants(w) for w in words])]
            for k in keys:
                self.places[k].append((RANK.get(p["place"], 1), p["pop"] or 0, p["raion"], p["name"], p["oblast"]))
        alias_file = DATA / "aliases.json"
        if alias_file.exists():
            for name, rid in json.load(open(alias_file)).items():
                for k in _product([stem_variants(w) for w in name.split()]):
                    self.places[" ".join(k)].append((5, 10**9, rid, name, rid[:4]))

    def lookup(self, key: str, scope: Optional[set[str]]) -> Optional[tuple]:
        cands = self.places.get(key)
        if not cands:
            return None
        if scope:
            scoped = [c for c in cands if c[4] in scope]
            if scoped:
                return max(scoped, key=lambda c: (c[0], c[1]))
            cands = [c for c in cands if c[0] >= 3 or c[1] >= 1500]  # outside scope: towns or big villages
        else:
            cands = [c for c in cands if c[0] >= 3 or c[1] >= 1500]
        return max(cands, key=lambda c: (c[0], c[1])) if cands else None


def _product(lists):
    out = [[]]
    for l in lists:
        out = [o + [x] for o in out for x in l]
    return out


@dataclass
class Event:
    ts: str
    msg_id: int
    raion: str
    kind: str          # jet|uav|cruise|ballistic|bomb|clear
    role: str          # current|target|origin|area|clear
    weight: float
    count: int
    text: str


class Parser:
    def __init__(self, gaz: Optional[Gazetteer] = None):
        self.g = gaz or Gazetteer()
        self.unmatched = Counter()

    # -- helpers
    def oblasts_in(self, text: str) -> list[str]:
        t = norm(text)
        found = []
        for code, stems in OBLAST_STEMS.items():
            if any(re.search(r"\b" + s, t) for s in stems):
                found.append(code)
        for adj, code in OBLAST_ADJ.items():
            if re.search(r"\b" + adj + r"\w*\s+обл", t):
                found.append(code)
        return found

    def header(self, text: str) -> tuple[Optional[set[str]], Optional[str], str]:
        """Returns (scope oblast codes, default raion, remaining text)."""
        m = re.match(r"^\s*(?:\d{1,2}:\d{2}\s*)?([^\n:]{2,40}):", text)
        if not m:
            return None, None, text
        head = norm(m.group(1))
        rest = text[m.end():]
        scope: set[str] = set()
        default = None
        if re.search(r"\bкиїв\b|києв|київ/", head):
            scope |= {"UA80", "UA32"}
            default = KYIV
        for code in self.oblasts_in(head):
            scope.add(code)
        if "загально" in head or not scope:
            return (scope or None), default, rest
        return scope, default, rest

    def kind_of(self, line: str) -> str:
        t = norm(line)
        for kind, rx in KIND_RX:
            if re.search(rx, t):
                return kind
        m = re.match(r"\s*([^\w\s])\ufe0f?", line)  # channel's own marker emoji, e.g. "🅿️1х далі Жуляни"
        if m:
            return EMOJI_KIND.get(m.group(1), "uav")
        return "uav"

    def places_in_line(self, line: str, scope: Optional[set[str]]) -> list[tuple[str, str, str]]:
        """-> [(raion_id, role, matched_name)]"""
        toks = TOKEN_RX.findall(line.translate(APOS))
        out = []
        role = "current"
        i = 0
        while i < len(toks):
            tok = toks[i]
            low = norm(tok)
            if low in ORIGIN_KW:
                role = "origin"; i += 1; continue
            if low in TARGET_KW:
                role = "target"; i += 1; continue
            if low in CURRENT_KW:
                role = "current"; i += 1; continue
            if tok in ("/",):
                i += 1; continue
            # named feature: "над Київським водосховищем", "з Чорного моря" — resolve the pair or skip both
            # words, never the adjective alone
            if tok[:1].isupper() and i + 1 < len(toks):
                noun = next((n for n in FEATURE_NOUNS if norm(toks[i + 1]).startswith(n)), None)
                if noun:
                    rid = FEATURES.get((feature_adj(tok), noun))
                    if rid:
                        out.append((rid, role, tok + " " + toks[i + 1]))
                    i += 2
                    continue
            # raion by adjective: "Броварському районі" / "Броварський р-н"
            if tok[:1].isupper() and i + 1 < len(toks) and norm(toks[i + 1]).startswith(("район", "р-н")):
                adj = adj_stem(tok)
                ids = self.g.raion_adj.get(adj, [])
                rid = next((x for x in ids if scope and x[:4] in scope), ids[0] if ids else None)
                if rid:
                    out.append((rid, role, tok + " район")); i += 2; continue
            # settlement: try 3,2,1-word capitalised n-grams
            if tok[:1].isupper() and low not in STOP:
                hit = None
                for n in (3, 2, 1):
                    if i + n > len(toks):
                        continue
                    gram = toks[i:i + n]
                    if not all(g[:1].isupper() for g in gram):
                        continue
                    if any(norm(g) in STOP for g in gram[1:]):
                        continue
                    for key in _product([stem_variants(g) for g in gram]):
                        hit = self.g.lookup(" ".join(key), scope)
                        if hit:
                            break
                    if hit:
                        out.append((hit[2], role, hit[3])); i += n; break
                if hit:
                    continue
                self.unmatched[tok] += 1
            i += 1
        return out

    # -- main
    def parse(self, msg_id: int, ts: str, text: str) -> list[Event]:
        text = text.translate(APOS).strip()
        if not text or IGNORE_RX.search(norm(text)):
            return []
        scope, default, rest = self.header(text)
        events: list[Event] = []
        for line in re.split(r"[\n.;]+|(?=\s*[🅿⚠🔄💣☄❗‼])", rest):
            line = line.strip()
            if not line:
                continue
            if CLEAR_RX.search(norm(line)):
                targets: set[str] = set()
                if default == KYIV or re.search(r"\bкиїв\b|\bкиєв", norm(line)):
                    targets.update(self.g.by_oblast["UA80"])
                elif default:
                    targets.add(default)
                for code in self.oblasts_in(line) + (list(scope or []) if not self.oblasts_in(line) else []):
                    targets.update(self.g.by_oblast.get(code, []))
                if re.search(r"\bобласть\b", norm(line)) and KYIV in targets:
                    targets.update(self.g.by_oblast["UA32"])
                for rid, role, _ in self.places_in_line(line, scope):
                    targets.update(self.g.by_oblast["UA80"] if rid == KYIV else [rid])
                events += [Event(ts, msg_id, r, "clear", "clear", 0.0, 0, line[:200]) for r in sorted(targets)]
                continue
            kind = self.kind_of(line)
            m = COUNT_RX.search(line)
            count = int(m.group(1)) if m else 1
            hits = self.places_in_line(line, scope)
            for code in self.oblasts_in(line):  # "у напрямку Київщини" -> whole oblast, low weight
                for rid in self.g.by_oblast.get(code, []):
                    hits.append((rid, "area", code))
            if not hits and default and (m or re.search(r"реактив|бпла|ракет|баліст", norm(line))):
                hits = [(default, "current", "header")]
            expanded = []
            for rid, role, name in hits:
                if rid == KYIV:
                    expanded += [(d, "city", name) for d in self.g.by_oblast["UA80"]]
                else:
                    expanded.append((rid, role, name))
            hits = expanded
            seen = set()
            for rid, role, name in hits:
                if (rid, role) in seen or any(h[0] == rid and h[1] not in ("area", "city") for h in hits if role in ("area", "city")):
                    continue
                seen.add((rid, role))
                events.append(Event(ts, msg_id, rid, kind, role, ROLE_WEIGHT[role], count, line[:200]))
        return events


def report(path: str):
    p = Parser()
    rows = [json.loads(l) for l in open(path)]
    n_threat = n_hit = 0
    kinds = Counter(); roles = Counter(); raions = Counter()
    misses = []
    for r in rows:
        evs = p.parse(r["id"], r["ts"], r["text"])
        is_threat = bool(COUNT_RX.search(r["text"])) and not IGNORE_RX.search(norm(r["text"]))
        if is_threat:
            n_threat += 1
            if any(e.kind != "clear" for e in evs):
                n_hit += 1
            else:
                misses.append(r)
        for e in evs:
            kinds[e.kind] += 1; roles[e.role] += 1
            if e.role in ("current", "target"):
                raions[p.g.raions[e.raion]["name"]] += 1
    print(f"messages {len(rows)}  threat-like {n_threat}  with>=1 raion {n_hit}  ({100*n_hit/max(1,n_threat):.0f}%)")
    print("kinds", kinds.most_common()); print("roles", roles.most_common())
    print("top raions", raions.most_common(15))
    print("UNMATCHED capitalised tokens:", p.unmatched.most_common(60))
    print("--- sample misses"); [print(" ", m["id"], m["text"][:120].replace("\n", " | ")) for m in misses[:25]]


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else str(DATA / "samples.jsonl"))
