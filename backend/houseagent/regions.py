"""Region master data with stable codes (JIS X 0401 / 0402). Display names in ja / zh / en."""

from __future__ import annotations

from typing import Any

# code, ja, zh, en
PREFECTURES: list[tuple[str, str, str, str]] = [
    ("01", "北海道", "北海道", "Hokkaido"),
    ("02", "青森県", "青森县", "Aomori"),
    ("03", "岩手県", "岩手县", "Iwate"),
    ("04", "宮城県", "宫城县", "Miyagi"),
    ("05", "秋田県", "秋田县", "Akita"),
    ("06", "山形県", "山形县", "Yamagata"),
    ("07", "福島県", "福岛县", "Fukushima"),
    ("08", "茨城県", "茨城县", "Ibaraki"),
    ("09", "栃木県", "栃木县", "Tochigi"),
    ("10", "群馬県", "群马县", "Gunma"),
    ("11", "埼玉県", "埼玉县", "Saitama"),
    ("12", "千葉県", "千叶县", "Chiba"),
    ("13", "東京都", "东京都", "Tokyo"),
    ("14", "神奈川県", "神奈川县", "Kanagawa"),
    ("15", "新潟県", "新潟县", "Niigata"),
    ("16", "富山県", "富山县", "Toyama"),
    ("17", "石川県", "石川县", "Ishikawa"),
    ("18", "福井県", "福井县", "Fukui"),
    ("19", "山梨県", "山梨县", "Yamanashi"),
    ("20", "長野県", "长野县", "Nagano"),
    ("21", "岐阜県", "岐阜县", "Gifu"),
    ("22", "静岡県", "静冈县", "Shizuoka"),
    ("23", "愛知県", "爱知县", "Aichi"),
    ("24", "三重県", "三重县", "Mie"),
    ("25", "滋賀県", "滋贺县", "Shiga"),
    ("26", "京都府", "京都府", "Kyoto"),
    ("27", "大阪府", "大阪府", "Osaka"),
    ("28", "兵庫県", "兵库县", "Hyogo"),
    ("29", "奈良県", "奈良县", "Nara"),
    ("30", "和歌山県", "和歌山县", "Wakayama"),
    ("31", "鳥取県", "鸟取县", "Tottori"),
    ("32", "島根県", "岛根县", "Shimane"),
    ("33", "岡山県", "冈山县", "Okayama"),
    ("34", "広島県", "广岛县", "Hiroshima"),
    ("35", "山口県", "山口县", "Yamaguchi"),
    ("36", "徳島県", "德岛县", "Tokushima"),
    ("37", "香川県", "香川县", "Kagawa"),
    ("38", "愛媛県", "爱媛县", "Ehime"),
    ("39", "高知県", "高知县", "Kochi"),
    ("40", "福岡県", "福冈县", "Fukuoka"),
    ("41", "佐賀県", "佐贺县", "Saga"),
    ("42", "長崎県", "长崎县", "Nagasaki"),
    ("43", "熊本県", "熊本县", "Kumamoto"),
    ("44", "大分県", "大分县", "Oita"),
    ("45", "宮崎県", "宫崎县", "Miyazaki"),
    ("46", "鹿児島県", "鹿儿岛县", "Kagoshima"),
    ("47", "沖縄県", "冲绳县", "Okinawa"),
]

# code, prefecture code, ja, zh, en
CITIES: list[tuple[str, str, str, str, str]] = [
    ("11101", "11", "さいたま市西区", "埼玉市西区", "Saitama Nishi-ku"),
    ("11102", "11", "さいたま市北区", "埼玉市北区", "Saitama Kita-ku"),
    ("11103", "11", "さいたま市大宮区", "埼玉市大宫区", "Saitama Omiya-ku"),
    ("11104", "11", "さいたま市見沼区", "埼玉市见沼区", "Saitama Minuma-ku"),
    ("11105", "11", "さいたま市中央区", "埼玉市中央区", "Saitama Chuo-ku"),
    ("11106", "11", "さいたま市桜区", "埼玉市樱区", "Saitama Sakura-ku"),
    ("11107", "11", "さいたま市浦和区", "埼玉市浦和区", "Saitama Urawa-ku"),
    ("11108", "11", "さいたま市南区", "埼玉市南区", "Saitama Minami-ku"),
    ("11109", "11", "さいたま市緑区", "埼玉市绿区", "Saitama Midori-ku"),
    ("11110", "11", "さいたま市岩槻区", "埼玉市岩槻区", "Saitama Iwatsuki-ku"),
    ("11203", "11", "川口市", "川口市", "Kawaguchi"),
    ("11208", "11", "所沢市", "所泽市", "Tokorozawa"),
    ("11219", "11", "上尾市", "上尾市", "Ageo"),
    ("11221", "11", "草加市", "草加市", "Soka"),
    ("11222", "11", "越谷市", "越谷市", "Koshigaya"),
    ("11224", "11", "戸田市", "户田市", "Toda"),
    ("11229", "11", "和光市", "和光市", "Wako"),
    ("12100", "12", "千葉市", "千叶市", "Chiba City"),
    ("12203", "12", "市川市", "市川市", "Ichikawa"),
    ("12204", "12", "船橋市", "船桥市", "Funabashi"),
    ("12207", "12", "松戸市", "松户市", "Matsudo"),
    ("12217", "12", "柏市", "柏市", "Kashiwa"),
    ("12227", "12", "浦安市", "浦安市", "Urayasu"),
    ("13101", "13", "千代田区", "千代田区", "Chiyoda"),
    ("13102", "13", "中央区", "中央区", "Chuo"),
    ("13103", "13", "港区", "港区", "Minato"),
    ("13104", "13", "新宿区", "新宿区", "Shinjuku"),
    ("13105", "13", "文京区", "文京区", "Bunkyo"),
    ("13106", "13", "台東区", "台东区", "Taito"),
    ("13107", "13", "墨田区", "墨田区", "Sumida"),
    ("13108", "13", "江東区", "江东区", "Koto"),
    ("13109", "13", "品川区", "品川区", "Shinagawa"),
    ("13110", "13", "目黒区", "目黑区", "Meguro"),
    ("13111", "13", "大田区", "大田区", "Ota"),
    ("13112", "13", "世田谷区", "世田谷区", "Setagaya"),
    ("13113", "13", "渋谷区", "涩谷区", "Shibuya"),
    ("13114", "13", "中野区", "中野区", "Nakano"),
    ("13115", "13", "杉並区", "杉并区", "Suginami"),
    ("13116", "13", "豊島区", "丰岛区", "Toshima"),
    ("13117", "13", "北区", "北区", "Kita"),
    ("13118", "13", "荒川区", "荒川区", "Arakawa"),
    ("13119", "13", "板橋区", "板桥区", "Itabashi"),
    ("13120", "13", "練馬区", "练马区", "Nerima"),
    ("13121", "13", "足立区", "足立区", "Adachi"),
    ("13122", "13", "葛飾区", "葛饰区", "Katsushika"),
    ("13123", "13", "江戸川区", "江户川区", "Edogawa"),
    ("14100", "14", "横浜市", "横滨市", "Yokohama"),
    ("14130", "14", "川崎市", "川崎市", "Kawasaki"),
    ("14150", "14", "相模原市", "相模原市", "Sagamihara"),
    ("14204", "14", "鎌倉市", "镰仓市", "Kamakura"),
    ("14205", "14", "藤沢市", "藤泽市", "Fujisawa"),
]

# Buying and renting use different property types; a task searches one deal type at a time.
DEALS: dict[str, tuple[str, ...]] = {
    "buy": ("used_mansion", "used_house", "land"),
    "rent": ("rent_apartment", "rent_house"),
}
PROPERTY_TYPES = DEALS["buy"] + DEALS["rent"]
TYPE_TO_DEAL = {t: d for d, types in DEALS.items() for t in types}
LAYOUTS = ("1R", "1K", "1DK", "1LDK", "2K", "2DK", "2LDK", "3K", "3DK", "3LDK", "4K", "4DK", "4LDK", "5LDK+")
SORT_ORDERS = ("newest", "price_asc", "price_desc", "area_desc")

PREFECTURE_CODES = {p[0] for p in PREFECTURES}
CITY_TO_PREF = {c[0]: c[1] for c in CITIES}


def city_name(code: str | None, lang: str = "ja") -> str | None:
    for c in CITIES:
        if c[0] == code:
            return {"ja": c[2], "zh": c[3], "en": c[4]}.get(lang, c[2])
    return None


def as_dict() -> dict[str, Any]:
    return {
        "prefectures": [{"code": p[0], "name": {"ja": p[1], "zh": p[2], "en": p[3]}} for p in PREFECTURES],
        "cities": [{"code": c[0], "prefecture": c[1], "name": {"ja": c[2], "zh": c[3], "en": c[4]}} for c in CITIES],
        "property_types": list(PROPERTY_TYPES),
        "deals": {d: list(t) for d, t in DEALS.items()},
        "layouts": list(LAYOUTS),
        "sort_orders": list(SORT_ORDERS),
    }
