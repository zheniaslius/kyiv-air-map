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
# "city": whole-city mention (Kyiv, Odesa, …) with no district resolved -> every district of that city at half weight

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
# "Дніпро і область дорозвідка до відбою" is the all-clear for a city *and its oblast*. The group is the 1-3
# capitalised words right before "та/і/й область": only they name the oblast, not other places on the line
# ("Київ та область чисто, 2х реактиви від Бобровиці" must not clear Chernihiv oblast).
CITY_AND_OBLAST_RX = re.compile(
    r"((?:[А-ЯІЇЄҐ][А-ЯІЇЄҐа-яіїєґ'\-]*\s+){0,2}[А-ЯІЇЄҐ][А-ЯІЇЄҐа-яіїєґ'\-]*)\s+(?:і|та|й)\s+област")
CITY_OBLAST = {"UA80": "UA32"}  # Kyiv city is its own oblast-level unit; "Київ та область" means Київська область
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
    "UA18": ["житомирщин"], "UA21": ["закарпатт"], "UA23": ["запоріжчин"], "UA26": ["прикарпатт", "франківщин"],
    "UA32": ["київщин"], "UA35": ["кіровоградщин"], "UA44": ["луганщин"], "UA46": ["львівщин"],
    "UA48": ["миколаївщин"], "UA51": ["одещин"], "UA53": ["полтавщин"], "UA56": ["рівненщин"],
    "UA59": ["сумщин"], "UA61": ["тернопільщин"], "UA63": ["харківщин"], "UA65": ["херсонщин"],
    "UA68": ["хмельниччин"], "UA71": ["черкащин"], "UA73": ["буковин", "чернівеччин"], "UA74": ["чернігівщин"],
    "UA01": ["крим"],
}
# region names that are also a city's name. "Запоріжжя" / "у Запоріжжі" / "довкола Запоріжжя" is the city; only
# "на Запоріжжі", like "на Київщині", is the whole oblast.
OBLAST_PHRASES = {"UA23": [r"\bна\s+запоріжжі\b"]}
OBLAST_ADJ = {  # "<adj>ська область"
    "вінницьк": "UA05", "волинськ": "UA07", "дніпропетровськ": "UA12", "донецьк": "UA14", "житомирськ": "UA18",
    "закарпатськ": "UA21", "запорізьк": "UA23", "івано-франківськ": "UA26", "київськ": "UA32", "кіровоградськ": "UA35",
    "луганськ": "UA44", "львівськ": "UA46", "миколаївськ": "UA48", "одеськ": "UA51", "полтавськ": "UA53",
    "рівненськ": "UA56", "сумськ": "UA59", "тернопільськ": "UA61", "харківськ": "UA63", "херсонськ": "UA65",
    "хмельницьк": "UA68", "черкаськ": "UA71", "чернівецьк": "UA73", "чернігівськ": "UA74",
}
# an oblast adjective in any case: "Запорізької області", "Херсонської та Миколаївської областей"
OBLAST_ADJ_TOK = re.compile(r"[сцз]ьк(?:а|ої|ій|у|ою|і|их|им|ими)$")
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
# "🟡 Жовтий рівень" / "🔴 Червоний рівень" (alert levels): the colour stems to a village (Жовте, Червоне). The pair
# is skipped; putting the colours in STOP would also kill "Зелений Гай", "Червоний Яр", "Жовтий Яр".
LEVEL_NOUNS = {"рівень", "рівня", "рівнем", "рівні"}
# "Дніпро" is also the river. The channel means the city, so the river is recognised only from unambiguous context:
# "вздовж Дніпра", "з лівобережжя Дніпра", or "над/через Дніпром" under a header of another oblast.
RIVER_RX = re.compile(r"дніпр(?:о|а|у|ом|і)")
RIVER_CONTEXT = {"вздовж", "уздовж", "русло", "русла", "руслу", "руслом", "руслі", "берег", "берега", "березі",
                 "лівобережжя", "правобережжя", "лівобережжі", "правобережжі", "річка", "річки", "річку", "річкою",
                 "річці"}
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


def stem_variants(word: str) -> list[str]:
    """Stem plus і→о/е/є alternation in the last syllable (Київ→Києва, Ріг→Рогу, Ірпінь→Ірпеня).

    Ordered, plain stem first: callers take the first key that hits, and a set (hash-seed order) let "Оріхів" hit
    the Kyiv alias "Орохів" on some process starts instead of the Zaporizhzhia town."""
    s = stem(word)
    out = [s]
    i = max(s.rfind("і"), s.rfind("ї"))
    if i > 0 and not re.search(r"[аеиоуіїяюєь]", s[i + 1:]):
        for v in "оеє":
            alt = s[:i] + v + s[i + 1:]
            if alt not in out:
                out.append(alt)
    return out


def adj_stem(word: str) -> str:
    """Stem for adjectival raion names: Броварському/Броварського/Броварський -> броварськ."""
    return re.sub(r"(ий|ого|ому|им|ім|а|ої|ій|ою)$", "", norm(word))


def feature_adj(word: str) -> str:
    """Any case of a -ський adjective -> its stem: Київським/Київського/Київське -> київськ."""
    return re.sub(r"(ськ)\w*$", r"\1", norm(word))


RANK = {"city": 4, "town": 3, "village": 2, "hamlet": 1, "urban": 2}
# Oblique case of an adjectival -ське/-ський name: "у Кам'янському", "від Покровського". stem() cuts these to
# "кам'ян"/"покров" (the villages Кам'яне, the town Покров); the "ськ" core is the nominative's key instead.
OBLIQUE_ADJ_RX = re.compile(r"^(.{3,}[сц]ьк)(?:ого|ому|им)$")
ADJ_NAME_RX = re.compile(r"[сц]ьк(?:ий|е)$")  # Покровське, not Покровськ, which shares the key "покровськ"


def _size(c: tuple) -> tuple:
    return c[0], c[1]


class Gazetteer:
    def __init__(self):
        self.raions = {r["id"]: r for r in json.load(open(DATA / "raions.json"))}
        self.by_oblast: dict[str, list[str]] = defaultdict(list)
        for r in self.raions.values():
            self.by_oblast[r["oblast"]].append(r["id"])
        # split city aggregate id -> its districts (raions.json "city_id"); the city itself has no polygon
        self.city_districts: dict[str, list[str]] = defaultdict(list)
        for r in self.raions.values():
            if r.get("city_id"):
                self.city_districts[r["city_id"]].append(r["id"])
        # raion adjective stem -> ids  ("Броварський район" -> "броварськ"; city districts included)
        self.raion_adj: dict[str, list[str]] = defaultdict(list)
        for r in self.raions.values():
            m = re.match(r"(.+?)(ий|а)\s+район", norm(r["name"]))
            if m:
                self.raion_adj[m.group(1)].append(r["id"])
        # settlement stem -> [(rank, pop, raion_id, name, oblast, derived)]
        self.places: dict[str, list[tuple]] = defaultdict(list)
        for p in json.load(open(DATA / "places.json")):
            words = p["name"].split()
            cand = (RANK.get(p["place"], 1), p["pop"] or 0, p["raion"], p["name"], p["oblast"])
            for k in _product([stem_variants(w) for w in words]):
                self.places[" ".join(k)].append(cand + (False,))
            # locative/dative of -ка nouns (к->ц): "у Василівці", "у Якимівці". Adjectives (-ська) decline to -ській.
            if re.search(r"[^ь]ка$", words[-1]):
                loc = words[:-1] + [words[-1][:-2] + "ці"]
                for k in _product([stem_variants(w) for w in loc]):
                    self.places[" ".join(k)].append(cand + (True,))
        alias_file = DATA / "aliases.json"
        if alias_file.exists():
            for name, rid in json.load(open(alias_file)).items():
                for k in _product([stem_variants(w) for w in name.split()]):
                    self.places[" ".join(k)].append((5, 10**9, rid, name, rid[:4], False))

    def lookup(self, key: str, scope: Optional[set[str]], keep=None) -> Optional[tuple]:
        cands = self.places.get(key)
        if cands and keep:
            cands = [c for c in cands if keep(c)]
        if not cands:
            return None
        # a real name always beats a derived locative; a derived locative outside the header scope must be a town,
        # since "У Григорівці" alone could be any of dozens of villages
        return (_pick([c for c in cands if not c[5]], scope, big_village=True)
                or _pick([c for c in cands if c[5]], scope, big_village=False))


def _pick(cands: list[tuple], scope: Optional[set[str]], big_village: bool) -> Optional[tuple]:
    if not cands:
        return None
    real = [c for c in cands if c[0] < 5]
    # a city-neighbourhood alias ("П'ятихатки" in Kyiv) speaks only for its own city: unless the header puts that
    # city in scope, a real town of the same name (П'ятихатки, Dnipropetrovsk oblast) wins
    if any(c[0] >= 3 for c in real) and not (scope and any(c[0] == 5 and c[4] in scope for c in cands)):
        cands = real
    if scope:
        scoped = [c for c in cands if c[4] in scope]
        if scoped:
            best = max(scoped, key=_size)
            # A header only scopes, it doesn't prove the place is local: "Миколаївщина: ... курсом на Київ" is the
            # capital, not a 17-person Київ in Вознесенський район. A small in-scope village (one that would not
            # count outside scope) loses to a city elsewhere or to a town 20x its size; bigger villages such as
            # frontline Кам'янське in Запорізька (2.6k) keep the local reading.
            if best[0] <= 2 and best[1] < 1500:
                big = [c for c in real if c[4] not in scope and
                       (c[0] == 4 or (c[0] == 3 and c[1] >= 10000 and c[1] >= 20 * max(best[1], 1)))]
                if big:
                    return max(big, key=_size)
            return best
    cands = [c for c in cands if c[0] >= 3 or (big_village and c[1] >= 1500)]  # outside scope: towns or big villages
    return max(cands, key=_size) if cands else None


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
        for code, rxs in OBLAST_PHRASES.items():
            if code not in found and any(re.search(rx, t) for rx in rxs):
                found.append(code)
        return found

    def city_header(self, head: str) -> Optional[tuple]:
        """"Харків:", "Кривий Ріг:" -> the gazetteer entry of the town or city the header names, else None."""
        toks = [t for t in TOKEN_RX.findall(head.translate(APOS)) if t != "/"]
        if not 1 <= len(toks) <= 3 or not all(t[:1].isupper() for t in toks) or norm(toks[0]) in STOP:
            return None
        for key in _product([stem_variants(t) for t in toks]):
            hit = self.g.lookup(" ".join(key), None)
            if hit:
                return hit if 3 <= hit[0] <= 4 else None
        return None

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
        else:
            city = self.city_header(m.group(1))  # "Одеса:", "Харків:" -> the city (its districts) and its oblast
            if city:
                scope.add(city[4])
                default = city[2]
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
        m = re.match(r"\s*([^\w\s])️?", line)  # channel's own marker emoji, e.g. "🅿️1х далі Жуляни"
        if m:
            return EMOJI_KIND.get(m.group(1), "uav")
        return "uav"

    def pick_raion(self, ids: list[str], after: str, scope: Optional[set[str]], cities: set[str]) -> Optional[str]:
        """Choose among same-named raions and city districts (Шевченківський: Kyiv, Kharkiv, Dnipro, Zaporizhzhia;
        Дніпровський: the Dnipropetrovsk raion and city districts). In order: the place named right after "район"
        ("…району Києва", "…району Дніпропетровщини"), a city named elsewhere on the line, the header scope (a
        Київщина header also covers Kyiv's districts, where the channel files them) preferring a real raion, then a
        real raion anywhere, then Kyiv's district, which the channel reports in the most detail."""
        if not ids:
            return None
        city_of = lambda x: self.g.raions[x].get("city_id")
        oblast = lambda x: self.g.raions[x]["oblast"]
        after_city = None
        if after[:1].isupper():
            for key in stem_variants(after):
                hit = self.g.lookup(key, None)
                if hit:
                    after_city = hit[2]
                    break
        named = {"UA80"} if norm(after).startswith(("києв", "київ")) else set(self.oblasts_in(after))
        wide = set(scope or ()) | ({"UA80"} if scope and "UA32" in scope else set())
        for ok in (lambda x: after_city is not None and city_of(x) == after_city,
                   lambda x: oblast(x) in named,
                   lambda x: city_of(x) in cities,
                   lambda x: oblast(x) in wide and not city_of(x),
                   lambda x: oblast(x) in wide,
                   lambda x: not city_of(x),
                   lambda x: oblast(x) == "UA80"):
            rid = next((x for x in ids if ok(x)), None)
            if rid:
                return rid
        return ids[0]

    def oblique_adj(self, tok: str, scope: Optional[set[str]]) -> Optional[tuple]:
        """"у Кам'янському" / "від Покровського": a town with an adjectival name (Кам'янське, Покровське), else a raion
        in scope with "район" left out ("Одеса: у Приморському"), else a village with such a name — never an alias
        such as Kyiv's "Харківський" masyv. None falls back to the generic stem."""
        m = OBLIQUE_ADJ_RX.match(norm(tok))
        if not m:
            return None
        town = self.g.lookup(m.group(1), scope, keep=lambda c: c[0] < 5 and ADJ_NAME_RX.search(norm(c[3])))
        if town and town[0] >= 3 and (not scope or town[4] in scope):
            return town
        if scope:
            ids = [x for x in self.g.raion_adj.get(adj_stem(tok), []) if x[:4] in scope]
            rid = self.pick_raion(ids, "", scope, set())
            if rid:
                return (0, 0, rid, tok, rid[:4], False)
        return town

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
            # the river Дніпро, not the city
            if i > 0 and RIVER_RX.fullmatch(low):
                prev = norm(toks[i - 1])
                if prev in RIVER_CONTEXT or (prev in ("над", "через") and scope and "UA12" not in scope):
                    i += 1; continue
            # alert-level legend: "🟡 Жовтий рівень" is not the village Жовте
            if tok[:1].isupper() and i + 1 < len(toks) and norm(toks[i + 1]) in LEVEL_NOUNS:
                i += 2; continue
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
            # oblast by adjective: "Запорізької області", "Херсонської та Миколаївської областей" — left to
            # oblasts_in(); looked up as places they stem to a city (Миколаїв) or an in-scope village (Запорізьке)
            j = i
            while (OBLAST_ADJ_TOK.search(norm(toks[j])) and toks[j][:1].isupper() and j + 2 < len(toks)
                   and norm(toks[j + 1]) in ("та", "і", "й", "/") and OBLAST_ADJ_TOK.search(norm(toks[j + 2]))):
                j += 2
            if (tok[:1].isupper() and OBLAST_ADJ_TOK.search(low) and j + 1 < len(toks)
                    and norm(toks[j + 1]).startswith("обл")):
                i = j + 2; continue
            # raion by adjective: "Броварському районі" / "Шевченківський р-н" — same-named ones resolved below
            if tok[:1].isupper() and i + 1 < len(toks) and norm(toks[i + 1]).startswith(("район", "р-н")):
                ids = self.g.raion_adj.get(adj_stem(tok), [])
                if ids:
                    after = toks[i + 2] if i + 2 < len(toks) else ""
                    out.append(((tuple(ids), after), role, tok + " район")); i += 2; continue
            # settlement: try 3,2,1-word capitalised n-grams. A STOP word may still open a longer name ("Нова Одеса",
            # "Нові Санжари"): only the bare 1-word gram is refused, so "Нова" never falls through to "Одеса"
            if tok[:1].isupper():
                hit = None
                for n in (3, 2, 1):
                    if i + n > len(toks) or (n == 1 and low in STOP):
                        continue
                    gram = toks[i:i + n]
                    if not all(g[:1].isupper() for g in gram):
                        continue
                    if any(norm(g) in STOP for g in gram[1:]):
                        continue
                    if n == 1:
                        hit = self.oblique_adj(tok, scope)
                    for key in ([] if hit else _product([stem_variants(g) for g in gram])):
                        hit = self.g.lookup(" ".join(key), scope)
                        if hit:
                            break
                    if hit:
                        out.append((hit[2], role, hit[3])); i += n; break
                if hit:
                    continue
                if low not in STOP:
                    self.unmatched[tok] += 1
            i += 1
        # same-named raions / city districts: decided once every city named on the line is known
        cities = {rid for rid, _, _ in out if isinstance(rid, str) and rid in self.g.city_districts}
        cities |= {self.g.raions[rid].get("city_id") for rid, _, _ in out if isinstance(rid, str) and rid in self.g.raions}
        return [(self.pick_raion(list(rid[0]), rid[1], scope, cities) if isinstance(rid, tuple) else rid, role, name)
                for rid, role, name in out]

    # -- main
    def parse(self, msg_id: int, ts: str, text: str) -> list[Event]:
        text = text.translate(APOS).strip()
        if not text or IGNORE_RX.search(norm(text)):
            return []
        scope, default, rest = self.header(text)
        head_oblasts = self.oblasts_in(text[: len(text) - len(rest)])  # oblasts the header itself names
        events: list[Event] = []
        for line in re.split(r"[\n.;]+|(?=\s*[🅿⚠🔄💣☄❗‼])", rest):
            line = line.strip()
            if not line:
                continue
            if CLEAR_RX.search(norm(line)):
                targets: set[str] = set()
                named = self.places_in_line(line, scope)
                if re.search(r"\bкиїв\b|\bкиєв", norm(line)):
                    targets.update(self.g.city_districts[KYIV])
                if default:
                    targets.update(self.g.city_districts.get(default) or [default])
                # an oblast named on the line wins; else, if the line names no place ("Київщина:\nчисто"), the
                # header's oblasts — under a city header ("Одеса:") only those the header itself names
                for code in self.oblasts_in(line) or ([] if named else head_oblasts if default else sorted(scope or [])):
                    targets.update(self.g.by_oblast.get(code, []))
                mo = CITY_AND_OBLAST_RX.search(line)
                city = self.places_in_line(mo.group(1), scope) if mo else []
                if city:  # "Миколаїв та область - відбій" -> every raion of the oblast holding that city
                    code = self.g.raions[city[-1][0]]["oblast"] if city[-1][0] in self.g.raions else city[-1][0][:4]
                    targets.update(self.g.by_oblast.get(CITY_OBLAST.get(code, code), []))
                for rid, role, _ in named:
                    targets.update(self.g.city_districts.get(rid) or [rid])
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
                if rid in self.g.city_districts:
                    expanded += [(d, "city", name) for d in self.g.city_districts[rid]]
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
