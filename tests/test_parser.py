import pytest
from ingest.parser import Parser

KYIV = "UA80000000000093317"
SOLOMIANSKYI = "UA80000000000980793"
DESNIANSKYI = "UA80000000000336424"
KYIV_ALL = {"UA80000000000126643", "UA80000000000980793", "UA80000000000875983", "UA80000000000210193", "UA80000000000479391", "UA80000000000336424", "UA80000000000551439", "UA80000000000719633", "UA80000000000624772", "UA80000000001078669"}
BROVARY = "UA32060000000012455"
BORYSPIL = "UA32040000000054694"
VYSHHOROD = "UA32100000000065867"
ODESA = "UA51100000000095786"
DNIPRO = "UA12020000000052809"

p = Parser()


def ev(text, **kw):
    return p.parse(1, "2026-09-11T19:00:00+00:00", text)


def raions(evs, roles=("current",)):
    return {e.raion for e in evs if e.role in roles}


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
    ("⚠️ Одеса - БпЛА з моря", ODESA),
    ("🅿️Харків 1х мгКР Бандероль", "UA63120000000091135"),
    ("Харківщина:\n⚠️ 4х БпЛА Герань-2 сектор Балаклія.", "UA63040000000023521"),
    ("🅿️Суми 2х реактива на місто", "UA59080000000057897"),
    ("⚠️ 3х БпЛА Герань-2 сектор Охтирка", "UA59040000000045652"),
    ("🅿️ Чернігів — мгКР Бандероль", "UA74100000000047140"),
    ("🅿️ 2х мгКР Бандероль повз Срібне", "UA74080000000030554"),
])
def test_enabled_oblasts_resolve(text, raion):
    """The oblasts switched on after Kyiv (Odesa, Kharkiv, Sumy, Chernihiv) land in the right raion."""
    assert raions(ev(text)) == {raion}


def test_oblast_header_town():
    assert raions(ev("Київщина:\n🅿️1х реактив Бровари")) == {BROVARY}


def test_raion_locative_and_genitive():
    assert raions(ev("Київщина:\n🅿️1х реактив у Броварському районі.")) == {BROVARY}
    evs = ev("Дніпропетровщина чисто.\n\n🅿️1х реактив від Берестина у напрямку Дніпровського району.")
    assert DNIPRO in raions(evs, roles=("target",))
    assert any(e.kind == "clear" for e in evs)


def test_direction_roles():
    evs = ev("Київщина:\n🅿️ 2х реактиви повз Славутич у напрямку Бровари.")
    cur = {e.raion for e in evs if e.role == "current"}
    tgt = {e.raion for e in evs if e.role == "target"}
    assert cur == {VYSHHOROD} and tgt == {BROVARY}
    assert all(e.count == 2 for e in evs)


def test_kyiv_declension():
    assert raions(ev("1х реактив зараз над Києвом на досить великій висоті."), roles=("city",)) == KYIV_ALL


def test_kryvyi_rih_alternation():
    evs = ev("🅿️1х реактив від Кривого Рогу у напрямку Одеса.")
    assert {e.role for e in evs} >= {"origin", "target"}
    assert ODESA in raions(evs, roles=("target",))


def test_clear_then_threat_same_message():
    evs = ev("Київ чисто\n\n🅿️1х повз Димер")
    assert {e.raion for e in evs if e.kind == "clear"} >= KYIV_ALL
    assert VYSHHOROD in raions(evs)


def test_oblast_mention_is_area_only():
    evs = ev("🅿️2х реактиви повз Славутич у напрямку Київщини.")
    assert VYSHHOROD in raions(evs)
    assert BORYSPIL in raions(evs, roles=("area",))


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
