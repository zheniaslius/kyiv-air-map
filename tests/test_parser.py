import json
import os
import pathlib
import subprocess
import sys

import pytest
from ingest.parser import Parser

KYIV = "UA80000000000093317"
SOLOMIANSKYI = "UA80000000000980793"
DESNIANSKYI = "UA80000000000336424"
DARNYTSKYI = "UA80000000000210193"
DNIPROVSKYI_KYIV = "UA80000000000479391"
SHEVCHENKIVSKYI_KYIV = "UA80000000001078669"
KYIV_ALL = {"UA80000000000126643", "UA80000000000980793", "UA80000000000875983", "UA80000000000210193", "UA80000000000479391", "UA80000000000336424", "UA80000000000551439", "UA80000000000719633", "UA80000000000624772", "UA80000000001078669"}
BROVARY = "UA32060000000012455"
BORYSPIL = "UA32040000000054694"
VYSHHOROD = "UA32100000000065867"
OBUKHIV = "UA32120000000081110"
ODESA = "UA51100000000095786"  # Одеський район, i.e. the suburbs once the city is cut out
PRYMORSKYI_ODESA = "UA51100270010320268"
ODESA_ALL = {"UA51100270010196805", "UA51100270010275193", PRYMORSKYI_ODESA, "UA51100270010413116"}
DNIPRO = "UA12020000000052809"  # Дніпровський район, around the city
KAMIANSKE = "UA12040000000032213"
KRYVYI_RIH = "UA12060000000022633"
NIKOPOL = "UA12080000000023578"
SYNELNYKOVE = "UA12140000000011720"
ZAPORIZKYI = "UA23060000000070350"
POLOHIVSKYI = "UA23100000000034132"  # Оріхів
VASYLIVKA = "UA23040000000067730"   # Василівський район, holds Дніпрорудне
MELITOPOLSKYI = "UA23080000000090746"
BASHTANKA = "UA48020000000033544"
VOZNESENSK = "UA48040000000011780"
MYKOLAIV = "UA48060000000094390"    # Миколаївський район, around the city
PERVOMAISK = "UA48080000000082320"
KROPYVNYTSKYI = "UA35040000000034705"
CHERKASKYI = "UA71080000000036344"
CHERNIHIV = "UA74100000000047140"
# split cities (KATOTTG of the city, expanded to its districts)
KHARKIV_C = "UA63120270010096107"
DNIPRO_C = "UA12020010010037010"
ZAPORIZHZHIA_C = "UA23060070010069526"
KRYVYI_RIH_C = "UA12060170010065850"
MYKOLAIV_C = "UA48060150010035747"
KAMIANSKE_C = "UA12040150010056523"
SUMY_C = "UA59080270010036634"
CHERNIHIV_C = "UA74100390010054825"
SHEVCHENKIVSKYI_KHARKIV = "UA63120270010948820"
KHORTYTSKYI = "UA23060070010618511"
SAMARSKYI_DNIPRO = "UA12020010010475293"
DNIPROVSKYI_KAMIANSKE = "UA12040150010118924"

p = Parser()


def ev(text, **kw):
    return p.parse(1, "2026-09-11T19:00:00+00:00", text)


def raions(evs, roles=("current",)):
    return {e.raion for e in evs if e.role in roles}


def clears(text):
    return {e.raion for e in ev(text) if e.kind == "clear"}


def oblast(code):
    return set(p.g.by_oblast[code])


def city(cid):
    return set(p.g.city_districts[cid])


def names(ids):
    return {p.g.raions[r]["name"] for r in ids}


def test_kyiv_header_neighbourhood_maps_to_district():
    assert raions(ev("Київ: 🅿️1х Троєщина")) == {DESNIANSKYI}
    assert raions(ev("Київ: 🅿️1х далі Жуляни")) == {SOLOMIANSKYI}
    assert raions(ev("Київщина:\n🅿️ 1х реактив Голосіївський район Києва.")) == {"UA80000000000126643"}


def test_kyiv_unknown_place_spreads_over_all_districts():
    evs = ev("Київ: 🅿️1х реактив Міський масив")
    assert raions(evs, roles=("city",)) == KYIV_ALL and all(e.weight == 0.5 for e in evs)


def test_named_water_body_is_not_the_city():
    """"Київським водосховищем" must not stem to Київ and light every district."""
    evs = ev("Київщина:\n⚠️ 4х БпЛА над Київським водосховищем")
    assert raions(evs) == {VYSHHOROD} and not KYIV_ALL & {e.raion for e in evs}
    assert raions(ev("Київщина:\n⚠️ 8х БпЛА від Київського водосховища"), roles=("origin",)) == {VYSHHOROD}


def test_unknown_named_feature_is_dropped():
    """"Чорного моря" is offshore: better nothing than the village the adjective used to match."""
    assert raions(ev("Одещина:\n⚠️ 2х БпЛА з Чорного моря у напрямку Одеси"), roles=("origin",)) == set()


@pytest.mark.parametrize("text, raion", [
    ("Одещина:\n⚠️ 6х БпЛА Ізмаїльський район.", "UA51080000000061776"),
    ("Одещина:\n🅿️1х реактив Усатове.", ODESA),
    ("Харківщина:\n⚠️ 2х БпЛА повз Люботин", "UA63120000000091135"),
    ("Харківщина:\n⚠️ 4х БпЛА Герань-2 сектор Балаклія.", "UA63040000000023521"),
    ("Сумщина:\n⚠️ 2х БпЛА повз Білопілля", "UA59080000000057897"),
    ("⚠️ 3х БпЛА Герань-2 сектор Охтирка", "UA59040000000045652"),
    ("Чернігівщина:\n🅿️ 1х реактив повз Седнів", CHERNIHIV),
    ("🅿️ 2х мгКР Бандероль повз Срібне", "UA74080000000030554"),
])
def test_enabled_oblasts_resolve(text, raion):
    """Towns in the enabled oblasts land in the right raion (their cities are split, see below)."""
    assert raions(ev(text)) == {raion}


# -- big cities are drawn by their districts

@pytest.mark.parametrize("text, cid", [
    ("🅿️Харків 1х мгКР Бандероль", KHARKIV_C),
    ("🅿️Дніпро 2х Бандеролі", DNIPRO_C),
    ("❗️ Запоріжжя 2х БпЛА.", ZAPORIZHZHIA_C),
    ("🅿️1х реактив Кривий Ріг", KRYVYI_RIH_C),
    ("⚠️2х БпЛА у Кривому Розі.", KRYVYI_RIH_C),
    ("⚠️ 6х БпЛА на Миколаїв.", MYKOLAIV_C),
    ("Кам'янське: 2х реактиви над містом", KAMIANSKE_C),
    ("🅿️Суми 2х реактива на місто", SUMY_C),
    ("🅿️ Чернігів — мгКР Бандероль", CHERNIHIV_C),
])
def test_big_city_mention_spreads_over_its_districts(text, cid):
    evs = ev(text)
    assert raions(evs, roles=("city",)) == city(cid) and {e.role for e in evs} == {"city"}
    assert all(e.weight == 0.5 for e in evs)


def test_same_named_city_districts_follow_the_city_on_the_line():
    """"Шевченківський район" is a district of Kyiv, Kharkiv, Dnipro and Zaporizhzhia."""
    assert raions(ev("Харків — 3х КАБ, Шевченківський район")) == {SHEVCHENKIVSKYI_KHARKIV}
    assert raions(ev("Харківщина:\n⚠️ 2х БпЛА Шевченківський район")) == {SHEVCHENKIVSKYI_KHARKIV}
    assert raions(ev("🅿️1х реактив Шевченківський район.")) == {SHEVCHENKIVSKYI_KYIV}  # no context: Kyiv, as before
    assert raions(ev("Запоріжжя: удар по Хортицькому району")) == {KHORTYTSKYI}
    assert raions(ev("Дніпро: 🅿️ 1х реактив над Самарським районом")) == {SAMARSKYI_DNIPRO}
    assert raions(ev("Дніпропетровщина:\n⚠️ 2х БпЛА у Дніпровському районі")) == {DNIPRO}  # the raion, not a district
    assert raions(ev("🅿️ 1х реактив у напрямку Дніпровського району Кам'янського"), roles=("target",)) == {DNIPROVSKYI_KAMIANSKE}


# -- a header scopes lookups, it doesn't make every namesake local

@pytest.mark.parametrize("text, raion", [
    ("Дніпропетровщина:\n⚠️ 3х БпЛА на Олександрію.", "Олександрійський район"),
    ("Дніпропетровщина:\n🅿️2х реактиви у напрямку Лозової.", "Лозівський район"),
    ("Чернігівщина:\n🅿️ 1х реактив вектор Сміла / Канів.", "Черкаський район"),
])
def test_header_village_does_not_swallow_neighbouring_city(text, raion):
    """A tiny village inside the header oblast loses to a big city of the same name next door."""
    assert names(raions(ev(text), roles=("target",))) == {raion}


def test_header_village_does_not_swallow_split_city():
    evs = ev("Дніпропетровщина:\n🅿️2х реактиви у напрямку Запоріжжя.")  # not the 203-person village Запоріжжя
    assert raions(evs, roles=("city",)) == city(ZAPORIZHZHIA_C) and not any(e.raion[:4] == "UA12" for e in evs)


def test_header_scope_does_not_turn_big_towns_into_local_hamlets():
    """"Миколаївщина: ... на Київ" is the capital, not the 17-person Київ in Вознесенський район."""
    evs = ev("Миколаївщина:\n🅿️2х реактиви повз Первомайськ курсом на Київ.")
    assert raions(evs) == {PERVOMAISK} and raions(evs, roles=("city",)) == KYIV_ALL
    assert raions(evs, roles=("target",)) == set()
    assert raions(ev("Миколаївщина:\n⚠️3х БпЛА повз Вознесенськ у напрямку Знам'янки."), roles=("target",)) == {KROPYVNYTSKYI}
    # 43187: "Сміла" kept the first header's scope and became the 23-person Сміле in Новгород-Сіверський район
    evs = ev("Чернігівщина:\n🅿️ 2х реактиви з Славутича у напрямку Київщини.\n\nЧеркащина:\n🅿️ 1х реактив вектор Сміла / Канів.")
    assert raions(evs, roles=("target",)) == {CHERKASKYI}


def test_header_scope_keeps_real_local_villages():
    """Only small in-scope villages give way, and only to a city or a town 20x their size."""
    assert raions(ev("Миколаївщина:\n⚠️3х БпЛА повз Миколаївку")) == {BASHTANKA}  # Donetsk's Миколаївка is only 12x
    assert raions(ev("Запоріжжя:\n💣 2х КАБ на Кам'янське"), roles=("target",)) == {VASYLIVKA}  # 2.6k village, not the city
    assert raions(ev("Запорізька область:\n💣 КАБ на Кам'янське"), roles=("target",)) == {VASYLIVKA}


def test_kyiv_alias_does_not_beat_real_town():
    """"П'ятихатки" is a Kyiv alias and a Dnipropetrovsk town: only a Kyiv header picks the alias."""
    evs = ev("Загально:\n🅿️1х реактив повз П'ятихатки на Кам'янське.")
    assert names(raions(evs, roles=("current", "target"))) == {"Кам'янський район"}
    assert not KYIV_ALL & {e.raion for e in evs}
    assert raions(ev("Київ: 🅿️1х П'ятихатки")) == {SOLOMIANSKYI}


def test_oblast_header_town():
    assert raions(ev("Київщина:\n🅿️1х реактив Бровари")) == {BROVARY}


def test_raion_locative_and_genitive():
    assert raions(ev("Київщина:\n🅿️1х реактив у Броварському районі.")) == {BROVARY}
    evs = ev("Дніпропетровщина чисто.\n\n🅿️1х реактив від Берестина у напрямку Дніпровського району.")
    assert DNIPRO in raions(evs, roles=("target",))
    assert any(e.kind == "clear" for e in evs)


def test_river_dnipro_is_not_the_city():
    """"Дніпро" the river must not light the city or Дніпровський район of Дніпропетровщина."""
    evs = ev("🅿️1х реактив вздовж Дніпра у напрямку Києва.")
    assert not ({DNIPRO} | city(DNIPRO_C)) & {e.raion for e in evs}
    evs = ev("Київщина:\n🅿️1х реактив над Дніпром у напрямку Українки.")
    assert raions(evs) == set() and raions(evs, roles=("target",)) == {OBUKHIV}
    assert ev("Херсонщина:\n💣 КАБ з лівобережжя Дніпра.") == []
    # the city stays the city, and towns named after the river are untouched
    assert raions(ev("🔄 2х реактиви довкола Дніпра."), roles=("city",)) == city(DNIPRO_C)
    assert raions(ev("Дніпропетровщина:\n🅿️2х реактиви на північ від Дніпра."), roles=("city",)) == city(DNIPRO_C)
    assert raions(ev("Полтавщина:\n🅿️1х реактив від Дніпра на Полтаву."), roles=("city",)) == city(DNIPRO_C)
    assert raions(ev("Запоріжжя:\n💣 КАБ через Дніпрорудне.")) == {VASYLIVKA}


def test_same_named_raion_kyiv_district_or_oblast():
    """Дніпровський / Подільський are both Kyiv city districts and raions of other oblasts."""
    assert raions(ev("Київщина:\n🅿️ 1х реактив Дніпровський район Києва.")) == {DNIPROVSKYI_KYIV}
    assert raions(ev("Київщина:\n🅿️1х реактив у Дніпровському районі.")) == {DNIPROVSKYI_KYIV}
    assert raions(ev("🅿️1х реактив у Дніпровському районі Києва.")) == {DNIPROVSKYI_KYIV}
    assert raions(ev("Київщина:\n🅿️1х реактив Подільський район.")) == {"UA80000000000719633"}
    assert raions(ev("🅿️1х реактив у Дніпровському районі Дніпропетровщини.")) == {DNIPRO}
    evs = ev("🅿️1х мгКР Бандероль з Полтавщини у напрямку Дніпровського району Дніпропетровщини.")
    assert raions(evs, roles=("target",)) == {DNIPRO}


def test_direction_roles():
    evs = ev("Київщина:\n🅿️ 2х реактиви повз Славутич у напрямку Бровари.")
    cur = {e.raion for e in evs if e.role == "current"}
    tgt = {e.raion for e in evs if e.role == "target"}
    assert cur == {VYSHHOROD} and tgt == {BROVARY}
    assert all(e.count == 2 for e in evs)


def test_kyiv_declension():
    assert raions(ev("1х реактив зараз над Києвом на досить великій висоті."), roles=("city",)) == KYIV_ALL


def test_orikhiv_exact_stem_wins_over_alternation():
    """"Оріхів" (Zaporizhzhia frontline town) must not hit the Kyiv "Орохів" alias via і→о alternation."""
    assert raions(ev("💣 2х КАБ на Оріхів"), roles=("target",)) == {POLOHIVSKYI}
    assert clears("Оріхів чисто") == {POLOHIVSKYI}
    assert raions(ev("Київ: 🅿️1х реактив Осокорки / Орохів")) == {DARNYTSKYI}


ORIKHIV_SNIPPET = """
import json
from ingest.parser import Parser
p = Parser()
print(json.dumps({t: sorted({(e.raion, e.role) for e in p.parse(1, "2026-09-11T19:00:00+00:00", t)})
                  for t in ("💣 2х КАБ на Оріхів", "Оріхів чисто")}))
"""


@pytest.mark.parametrize("seed", ["1", "2"])
def test_alternation_order_independent_of_hash_seed(seed):
    """Variant order used to follow set iteration, so the result changed with PYTHONHASHSEED at server start."""
    root = pathlib.Path(__file__).resolve().parents[1]
    res = subprocess.run([sys.executable, "-c", ORIKHIV_SNIPPET], cwd=root, capture_output=True, text=True,
                         env={**os.environ, "PYTHONHASHSEED": seed}, check=True)
    out = json.loads(res.stdout)
    assert out["💣 2х КАБ на Оріхів"] == [[POLOHIVSKYI, "target"]]
    assert out["Оріхів чисто"] == [[POLOHIVSKYI, "clear"]]


def test_kryvyi_rih_alternation():
    evs = ev("🅿️1х реактив від Кривого Рогу у напрямку Одеса.")
    assert raions(evs, roles=("city",)) == city(KRYVYI_RIH_C) | ODESA_ALL


def test_odesa_mention_spreads_over_its_districts():
    evs = ev("🔄 1х реактив над Одесою.")
    assert raions(evs, roles=("city",)) == ODESA_ALL and all(e.weight == 0.5 for e in evs)
    assert raions(ev("Одеса: 🅿️ 2х реактиви на місто"), roles=("city",)) == ODESA_ALL  # header default


def test_odesa_clear_suburbs_and_district():
    assert clears("Одеса чисто.") == ODESA_ALL
    assert raions(ev("Одещина:\n🅿️1х реактив на Чорноморськ"), roles=("target",)) == {ODESA}
    assert raions(ev("Одещина:\n⚠️ 2х БпЛА у Приморському районі Одеси.")) == {PRYMORSKYI_ODESA}


# -- case forms

@pytest.mark.parametrize("text, expected", [
    ("Дніпропетровщина:\n💥 Вибухи у Кам'янському.", "kamianske"),     # not the villages Кам'яне
    ("🅿️2х реактиви від Кам'янського у напрямку Дніпра.", "kamianske"),  # not Кам'яне, Луганщина
    ("⚠️2х БпЛА у Кривому Розі.", "kryvyi_rih"),                        # not the village Крива, Закарпаття
    ("Дніпропетровщина:\n⚠️2х БпЛА у Кривому Розі.", "kryvyi_rih"),
    ("⚠️1х БпЛА над Кривим Рогом.", "kryvyi_rih"),
    ("Загально:\n🅿️1х реактив біля Покровського.", "synelnykove"),      # Покровське, not Покров or Покровськ
    ("Загально:\n🅿️1х реактив біля Марганця.", "nikopol"),
    ("⚠️1х БпЛА біля Шахтарського.", "synelnykove"),                    # Шахтарське, not Шахтарськ
])
def test_dnipropetrovsk_case_forms(text, expected):
    """Oblique cases of -ське names and of Кривий Ріг / Марганець resolve like the nominative."""
    want = {"kamianske": city(KAMIANSKE_C), "kryvyi_rih": city(KRYVYI_RIH_C),
            "synelnykove": {SYNELNYKOVE}, "nikopol": {NIKOPOL}}[expected]
    assert raions(ev(text), roles=("current", "origin", "target", "city")) >= want
    assert all(e.raion[:4] == "UA12" for e in ev(text))


def test_oblique_adjective_is_not_a_namesake():
    """The -ського/-ському rule must not trade one namesake for another in the live oblasts."""
    assert raions(ev("Одеса: 🅿️1х у Приморському")) == {PRYMORSKYI_ODESA}  # the district, not Приморське (Ізмаїл)
    assert raions(ev("Київщина:\n🅿️1х Бориспіль / Броварського")) == {BORYSPIL, BROVARY}
    assert not KYIV_ALL & {e.raion for e in ev("🅿️1х від Харківського у напрямку Полтави")}  # not the Kyiv masyv


@pytest.mark.parametrize("text, roles, raion", [
    ("💣 3х КАБ у напрямку Оріхова.", ("target",), POLOHIVSKYI),
    ("Запорізька область:\n💣 3х КАБ у напрямку Оріхова", ("target",), POLOHIVSKYI),
    ("💥 Вибухи в Оріхові", ("current",), POLOHIVSKYI),
    ("⚠️ 2х БпЛА з Оріхова на Запоріжжя", ("origin",), POLOHIVSKYI),
    ("⚠️ 2х БпЛА у Василівці", ("current",), VASYLIVKA),
    ("⚠️ 2х БпЛА у Якимівці", ("current",), MELITOPOLSKYI),
    ("⚠️ 2х БпЛА у Кам'янці-Дніпровській", ("current",), VASYLIVKA),
])
def test_zaporizhzhia_town_case_forms(text, roles, raion):
    """Оріхова/Оріхові used to stem to a Crimean village Оріхове; "у Василівці" matched nothing."""
    assert raions(ev(text), roles=roles) == {raion}


def test_locative_keeps_header_scope_and_skips_lone_villages():
    assert raions(ev("Київщина:\n⚠️ 2х БпЛА на Оріхове"), roles=("target",)) == {OBUKHIV}
    # no header and only villages of that name: too ambiguous to guess
    assert raions(ev("⚠️ 2х БпЛА у Григорівці")) == set()


# -- cities, their oblasts and their clears

def test_zaporizhzhia_city_is_not_the_oblast():
    """"Запоріжжя" is the city: it must not paint or clear the frontline raions. The oblast is written
    "Запорізька область", "Запоріжчина" or "на Запоріжжі"."""
    zap = city(ZAPORIZHZHIA_C)
    for text in ("🅿️ Запоріжжя 1х БпЛА", "❗️ Запоріжжя 2х БпЛА.", "🔄 3х керованих реактивних БпЛА довкола Запоріжжя.",
                 "Запоріжжя:\n⚠️ 3х БпЛА довкола міста"):
        assert {(e.raion, e.role) for e in ev(text)} == {(d, "city") for d in zap}
    for text in ("Запоріжжя чисто.", "Запоріжжя — відбій загрози КАБ.", "Запоріжжя:\nчисто"):
        assert clears(text) == zap
    assert raions(ev("Запоріжжя:\n💣 КАБ на Оріхів"), roles=("target",)) == {POLOHIVSKYI}
    for text in ("Запорізька область чисто.", "Запоріжчина чисто", "Запоріжжя і область чисто"):
        assert clears(text) == oblast("UA23")
    assert raions(ev("3х нових реактиви на Запоріжжі."), roles=("target", "current", "area", "city")) == oblast("UA23")
    dnipro_header = clears("Дніпропетровщина:\nЗапоріжжя чисто")  # a city clear doesn't zero the header oblast
    assert zap <= dnipro_header and DNIPRO not in dnipro_header


def test_city_and_oblast_clear_covers_the_oblast():
    """"<місто> та/і область — відбій" is the channel's all-clear for the city's oblast, not just the city."""
    assert clears("Дніпро і область дорозвідка до відбою.") == oblast("UA12")  # corpus 44348
    assert clears("Кривий Ріг та область чисто.") == oblast("UA12")
    assert clears("Миколаїв та область - відбій.") == oblast("UA48")
    assert clears("Київ та область дорозвідка до відбою.") == KYIV_ALL | oblast("UA32")  # city is UA80, oblast UA32
    assert clears("Одеса та область відбій.") == oblast("UA51")
    # only the city right before "та область" names the oblast (corpus 44104: Бобровиця is in Chernihiv oblast)
    assert CHERNIHIV not in clears("Київ та область чисто, 2х реактиви від Бобровиці заходять так само.")
    assert clears("Дніпро чисто.") == city(DNIPRO_C)  # no "область": the city only


def test_city_headers():
    assert raions(ev("Дніпро:\n🅿️ 2х реактиви над містом"), roles=("city",)) == city(DNIPRO_C)
    assert raions(ev("Миколаїв:\n⚠️3х БпЛА над містом."), roles=("city",)) == city(MYKOLAIV_C)
    assert raions(ev("Миколаїв:\n⚠️ 2х БпЛА на Вознесенськ."), roles=("target",)) == {VOZNESENSK}
    assert ev("Миколаївщина:\n⚠️ 4х БпЛА над містом") == []  # oblast header: no city default
    # "чисто" under a city header clears the city, not the rest of its oblast
    assert clears("Миколаїв: чисто.") == city(MYKOLAIV_C)
    assert clears("Одеса: чисто.") == ODESA_ALL
    assert clears("Київ/Київщина:\nЧисто.") == KYIV_ALL | oblast("UA32")  # the header names the oblast too


def test_nova_odesa_is_the_mykolaiv_town_not_odesa_city():
    """"Нова" is a STOP word, but it must still open "Нова Одеса" instead of dropping to "Одеса" (UA51 city)."""
    assert {(e.raion, e.role) for e in ev("Миколаївщина:\n🅿️1х реактив Нова Одеса.")} == {(MYKOLAIV, "current")}
    assert {(e.raion, e.role) for e in ev("🅿️1х реактив у напрямку Нова Одеса.")} == {(MYKOLAIV, "target")}
    assert clears("Нова Одеса чисто.") == {MYKOLAIV}
    evs = ev("⚠️2х БпЛА повз Нову Одесу на Вознесенськ.")
    assert raions(evs) == {MYKOLAIV} and raions(evs, roles=("target",)) == {VOZNESENSK}
    assert raions(ev("🅿️1х реактив Новий Буг.")) == {BASHTANKA}
    # a STOP word on its own, or later in the gram, still never matches
    assert raions(ev("Одеса:\n⚠️2х БпЛА Нова ціль Одеса"), roles=("city",)) == ODESA_ALL


@pytest.mark.parametrize("text, role", [
    ("🅿️2х реактиви повз Южноукраїнськ.", "current"),       # pre-2024 name of Південноукраїнськ
    ("🅿️1х реактив Південноукраїнськ.", "current"),
    ("⚠️3х БпЛА у напрямку Південноукраїнської АЕС.", "target"),  # -ської stems past the town's key
    ("⚠️3х БпЛА у напрямку Южноукраїнської АЕС.", "target"),
    ("⚠️1х БпЛА на ЮУАЕС.", "target"),
])
def test_south_ukraine_npp_town(text, role):
    """The nuclear plant town, under old, new and plant names, lands in Вознесенський район."""
    assert raions(ev(text), roles=(role,)) == {VOZNESENSK}
    assert clears("Южноукраїнськ чисто.") == {VOZNESENSK}


def test_clear_then_threat_same_message():
    evs = ev("Київ чисто\n\n🅿️1х повз Димер")
    assert {e.raion for e in evs if e.kind == "clear"} >= KYIV_ALL
    assert VYSHHOROD in raions(evs)


def test_oblast_mention_is_area_only():
    evs = ev("🅿️2х реактиви повз Славутич у напрямку Київщини.")
    assert VYSHHOROD in raions(evs)
    assert BORYSPIL in raions(evs, roles=("area",))


# -- words that are not places

@pytest.mark.parametrize("text", [
    "Запорізька область:\n⚠️ 3х БпЛА на півночі Запорізької області",       # village Запорізьке, Пологівський
    "Запоріжжя:\n⚠️ 2х БпЛА у Запорізькій області",
    "Дніпропетровщина:\n🅿️ 1х реактив з Запорізької області",               # village Запорізьке, Криворізький
    "Миколаївщина:\n⚠️ 2х БпЛА у Миколаївській обл.",
    "⚠️ 3х БпЛА на межі Херсонської та Миколаївської областей",             # "-ської" stems to Херсон / Миколаїв
    "Дніпропетровщина:\n⚠️ 3х БпЛА через Запорізьку / Дніпропетровську області",
    "Загроза для Одеської та Миколаївської областей",
])
def test_oblast_adjective_is_not_a_settlement(text):
    """"<adj> області" is the whole oblast (area), never the city or village its adjective stems to."""
    evs = ev(text)
    assert evs and {e.role for e in evs} == {"area"}


def test_oblast_adjective_keeps_real_places():
    assert raions(ev("Запорізька область:\n⚠️ 3х БпЛА у Запорізькій області"), roles=("area",)) == oblast("UA23")
    assert raions(ev("Дніпропетровщина:\n⚠️ 2х БпЛА з Запорізької області у напрямку Нікополя"), roles=("origin", "target")) == {NIKOPOL}
    assert raions(ev("🅿️1х реактивний Сікер у Кам'янському районі Дніпропетровської області.")) == {KAMIANSKE}
    assert raions(ev("⚠️ 2х БпЛА на Миколаїв, область"), roles=("city",)) == city(MYKOLAIV_C)  # a city before "область" stays
    assert raions(ev("🅿️ 1х реактив на Вознесенськ Миколаївської області"), roles=("target",)) == {VOZNESENSK}


def test_alert_level_colour_is_not_a_village():
    """The alert legend (43900): "Жовтий" stems to Жовте (Кам'янський р-н), "Червоний" to Червоне."""
    assert ev("Нові рівні оповіщення:\n🟡 Жовтий рівень:\n\"Дронова небезпека\".\n\n🔴 Червоний рівень:") == []
    assert ev("Дніпропетровщина:\n🔴 Червоний рівень") == []
    # colour words that really are place names still resolve
    assert raions(ev("Миколаївщина:\n⚠️ 2х БпЛА повз Зелений Гай")) == {MYKOLAIV}
    assert raions(ev("Одещина:\n⚠️ 2х БпЛА повз Жовтий Яр")) == {"UA51040000000032911"}


# -- regressions found by review, curated neighbourhoods of the split cities

SALTIVSKYI = "UA63120270010315719"
KYIVSKYI_KHARKIV = "UA63120270010216514"
KHOLODNOHIRSKYI = "UA63120270010877312"
DNIPROVSKYI_ZAP = "UA23060070010228148"
ZAVODSKYI_MYKOLAIV = "UA48060150010139573"
NOVOZAVODSKYI = "UA74100390010268220"
PODILSKYI_KYIV = "UA80000000000719633"


def test_kyiv_alias_beats_a_small_town_elsewhere():
    """Лісове (1.3k, Kirovohrad oblast) must not beat Kyiv's "Лісовий"; П'ятихатки (19k) still does (see above)."""
    assert raions(ev("🅿️1х Лісовий.")) == {DESNIANSKYI}
    assert raions(ev("🔄 1х Троєщина / Лісовий")) == {DESNIANSKYI}
    assert raions(ev("Київщина:\n🅿️1х Катеринівка")) == {"UA80000000000875983"}


@pytest.mark.parametrize("text, raion", [
    ("Харківщина:\n⚠️ 2х БпЛА Рубіжне", "Чугуївський район"),
    ("Харківщина:\n💣 КАБ по Рубіжному", "Чугуївський район"),
    ("Дніпропетровщина:\n🅿️1х Південне", "Нікопольський район"),
    ("Київщина:\n🅿️1х повз Гайворон", "Білоцерківський район"),
])
def test_local_village_under_its_header_is_not_a_far_town(text, raion):
    """The "far town" rule is for targets ("у напрямку Карлівка"); a drone over a village is in that oblast."""
    assert names(raions(ev(text))) == {raion}


def test_kyiv_district_with_a_suburb_on_the_line():
    assert raions(ev("Київ:\n🅿️1х реактив повз Бровари у напрямку Дніпровського району."), roles=("target",)) == {DNIPROVSKYI_KYIV}
    assert raions(ev("Київ:\n🅿️1х реактив з Вишгорода на Подільський район."), roles=("target",)) == {PODILSKYI_KYIV}


def test_coordinated_raion_adjectives():
    assert raions(ev("🅿️1х реактив у Дніпровському та Деснянському районах Києва.")) == {DNIPROVSKYI_KYIV, DESNIANSKYI}
    assert raions(ev("Дніпропетровщина:\n🅿️1х у Дніпровському та Кам'янському районах.")) == {DNIPRO, KAMIANSKE}
    assert raions(ev("Київщина:\n🅿️2х реактиви через Броварський, Бориспільський райони")) == {BROVARY, BORYSPIL}


def test_city_header_variants():
    assert raions(ev("м. Одеса:\n⚠️2х БпЛА"), roles=("city",)) == ODESA_ALL
    assert raions(ev("Одеса/Миколаїв:\n⚠️2х БпЛА у Приморському")) == {PRYMORSKYI_ODESA}
    assert clears("Одеса та область:\nчисто") == oblast("UA51")
    assert clears("Київ та область:\nдорозвідка до відбою") == KYIV_ALL | oblast("UA32")
    assert clears("Харків і область:\nчисто") == oblast("UA63")
    assert clears("Київ:\nчисто") == KYIV_ALL


def test_clear_remark_after_comma_is_not_cleared():
    assert clears("Київщина:\nчисто, 1х реактив від Славутича") == oblast("UA32")
    assert clears("Київщина:\nБровари чисто") == {BROVARY}
    assert "UA74040000000028062" not in clears("Київ та область чисто, 2х реактиви від Бобровиці заходять так само.")


def test_direction_adjective_is_not_a_place():
    assert ev("⚠️ 3х БпЛА з Черкаського напрямку.") == []
    assert raions(ev("Харківщина:\n⚠️ 3х БпЛА з Сумського напрямку."), roles=("origin",)) == set()


@pytest.mark.parametrize("text, roles, raion", [
    ("Харків: ⚠️ БпЛА на Салтівку", ("target",), SALTIVSKYI),
    ("💣 2х КАБ на Журавлівку", ("target",), KYIVSKYI_KHARKIV),
    ("⚠️ 2х БпЛА над Холодною горою", ("current",), KHOLODNOHIRSKYI),  # lowercase second word: aliases only
    ("Дніпро: КАБ на Ігрень", ("target",), SAMARSKYI_DNIPRO),
    ("⚠️ 2х БпЛА над ДніпроГЕС", ("current",), DNIPROVSKYI_ZAP),
    ("⚠️ 2х БпЛА над Дніпровською ГЕС", ("current",), DNIPROVSKYI_ZAP),
    ("Миколаїв: ⚠️ БпЛА на Корениху", ("target",), ZAVODSKYI_MYKOLAIV),
    ("Чернігівщина:\n⚠️ 2х БпЛА на Масанах", ("target",), NOVOZAVODSKYI),
])
def test_city_neighbourhoods(text, roles, raion):
    assert raions(ev(text), roles=roles) == {raion}


def test_oblique_cases_of_iv_names():
    """stem() cuts Харків to "харк" but Харкова/Харкові keep "харков"; Чернігів, Канів, Драбів likewise."""
    assert raions(ev("🅿️1х реактив від Чернігова у напрямку Києва."), roles=("city",)) >= city(CHERNIHIV_C)
    assert raions(ev("💥 Вибухи у Харкові"), roles=("city",)) == city(KHARKIV_C)
    assert names(raions(ev("🅿️1х реактив від Драбова у напрямку Київщини."), roles=("origin",))) == {"Золотоніський район"}


def test_district_of_an_unsplit_city_is_dropped():
    """Kherson's Корабельний район isn't split: it must not light Mykolaiv's district of that name."""
    assert not {e.raion for e in ev("Херсонщина:\n⚠️ 2х БпЛА над Корабельним районом")} & oblast("UA48")
    assert not {e.raion for e in ev("⚠️ 2х БпЛА над Корабельним районом Херсона")} & oblast("UA48")


def test_ignored_messages():
    assert ev("📡 Обстановка станом на 00:00\n— Стратегічна авіація:\nНе активна;") == []
    assert ev("Загальна оцінка загроз для України на ніч 12 вересня.\n🟩 Стратегічна авіація не активна.") == []


def test_kinds():
    assert {e.kind for e in ev("Полтавщина:\n🅿️3х мгКР Бандероль повз Полтаву.")} == {"cruise"}
    assert {e.kind for e in ev("‼️ Київ — спуск балістики! Друга")} == {"ballistic"}
    assert {e.kind for e in ev("Одещина:\n⚠️ 6х звичайних БпЛА Ізмаїльський район.")} == {"uav"}


def test_kind_from_channel_marker_emoji():
    assert {e.kind for e in ev("Київ: 🅿️1х далі Жуляни")} == {"jet"}
    assert {e.kind for e in ev("Київ: 🔄 1х Березняки / Позняки. Рух по колу.")} == {"jet"}
    assert {e.kind for e in ev("Одещина:\n⚠️ 6х Ізмаїльський район.")} == {"uav"}
    assert {e.kind for e in ev("🅿️2х мгКР Бандероль повз Полтаву.")} == {"cruise"}  # keyword wins over marker
