"""Generic listing extraction for browser-assisted import on sites without a dedicated parser.

Japanese property portals share a pattern: a result page is a repetition of "cards", each with a link to a
detail page (whose URL carries the listing ID), a price in 万円, an area in ㎡ and labelled rows such as
間取り / 所在地 / 交通 / 築年月. This module finds those cards structurally (the largest element that contains
exactly one detail ID and a price) and reads the fields from the card text. It is deliberately conservative:
cards without a detail link and a price are ignored, and a page with fewer than two cards is not treated as a
result list.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from houseagent.adapters.base import AssistedPage, NormalizedSource
from houseagent.regions import CITIES, PREFECTURES, TYPE_TO_DEAL

_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_BLOCK = {
    "div",
    "p",
    "li",
    "tr",
    "td",
    "th",
    "dt",
    "dd",
    "dl",
    "ul",
    "ol",
    "table",
    "section",
    "article",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
    "span",
    "a",
    "strong",
    "b",
    "em",
    "label",
}

# URL fragments that identify a listing's detail page and its ID
_ID_PATTERNS = [
    re.compile(r"nc_(\d{5,})"),  # SUUMO
    re.compile(r"/detail/(bk-[\w-]{8,})"),  # CHINTAI
    re.compile(r"/(b\d{7,})/?(?:[?#]|$)"),  # Yahoo!不動産 /detail_corp/b0012345678/
    re.compile(r"/(\d{7,})/?(?:[?#]|$)"),  # at home and many others: /kodate/1131490217/
    re.compile(r"[?&](?:bukken_?id|bkid|id|pid)=([\w-]{5,})", re.I),
]
_NOT_LISTING = re.compile(r"/(company|kaisha|shop|agent|review|kuchikomi|office|inquiry|map)(/|$)", re.I)
_GENERIC_TITLES = re.compile(
    r"^(詳細を見る|詳細|物件詳細|詳しく見る|もっと見る|詳細をみる|この物件を見る|間取図|間取り図|写真|画像.*|地図|"
    r"お気に入り.*|追加|問い?合わ?せ.*|[\d.,]+\s*万?円?)$"
)
_ADDRESS_NOISE = re.compile(r"\s*(周辺地図|地図を見る|地図で見る|MAP|マップ|地図)\s*$")
_NAV_WORDS = re.compile(r"/(list|search|page\d*|area|city|line|station)(/|$)|[?&]page=", re.I)
_PRICE = re.compile(r"(?:(\d+)\s*億)?\s*(\d[\d,]*(?:\.\d+)?)\s*万\s*円?|(\d+)\s*億\s*円")
_AREA = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m²|㎡|m2|平米|m\b)")
_LAYOUT = re.compile(r"(ワンルーム|\d\s*S?\s*(?:LDK|DK|K|R))")
_YEAR = re.compile(r"(\d{4})\s*年\s*\d{0,2}\s*月?")
_AGE = re.compile(r"築\s*(\d+)\s*年")
_WALK = re.compile(r"徒歩\s*(\d+)\s*分")
_STATION = re.compile(r"[「『]([^」』]{1,20})[」』]\s*駅?|(\S{1,12}?)駅\s*(?:徒歩|から|より)")
_FLOOR = re.compile(r"(?:/\s*|^|\s)(\d{1,2})\s*階(?!建)")

LABELS = {
    "price": ("販売価格", "価格", "賃料", "家賃"),
    "address": ("所在地", "住所"),
    "access": ("交通", "沿線・駅", "最寄駅", "アクセス", "駅徒歩"),
    "layout": ("間取り",),
    "built": ("築年月", "築年数", "完成時期", "建築年月"),
    "exclusive": ("専有面積", "面積"),
    "building": ("建物面積",),
    "land": ("土地面積",),
    "fee": ("管理費", "管理費等", "共益費", "管理費・共益費"),
    "deposit": ("敷金",),
    "key": ("礼金",),
    "floor": ("階建", "所在階", "階建/階"),
    "name": ("物件名", "建物名"),
}
_ALL_LABELS = {lab for labs in LABELS.values() for lab in labs}
TYPE_WORDS = [
    ("中古マンション", "used_mansion"),
    ("中古一戸建", "used_house"),
    ("中古住宅", "used_house"),
    ("中古戸建", "used_house"),
    ("土地", "land"),
    ("売地", "land"),
    ("賃貸一戸建", "rent_house"),
    ("賃貸マンション", "rent_apartment"),
    ("アパート", "rent_apartment"),
]


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: _Node | None = None
    children: list[_Node | str] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.cur = self.root
        self.skip = 0
        self.anchors: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag in _SKIP:
                self.skip += 1
            return
        if tag in _SKIP:
            self.skip = 1
            return
        node = _Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag == "a":
            self.anchors.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in _SKIP:
                self.skip -= 1
            return
        n: _Node | None = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.cur.children.append(data)


# Human-verification / access-block pages (CAPTCHA widgets, bot challenges). Used only to tell the user.
VERIFY = re.compile(
    r"captcha|recaptcha|hcaptcha|geetest|initGeetest|turnstile|認証にご協力|ロボットではありません|"
    r"Just a moment|cf-challenge|challenge-platform|px-captcha|perimeterx|アクセスが制限|不正なアクセス",
    re.I,
)


def looks_like_verification(html: str, status: int | None = None) -> str | None:
    """The signal that marks a human-verification / blocked page, or None."""
    if status in (401, 403, 405, 429):
        return f"HTTP {status}"
    m = VERIFY.search(html[:200_000])
    return m.group(0) if m else None


def listing_id(url: str) -> str | None:
    for pat in _ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None


def _tokens(node: _Node) -> list[str]:
    out: list[str] = []

    def walk(n: _Node) -> None:
        buf: list[str] = []
        for c in n.children:
            if isinstance(c, str):
                buf.append(c)
            else:
                if buf:
                    out.append(" ".join(buf))
                    buf = []
                walk(c)
        if buf:
            out.append(" ".join(buf))

    walk(node)
    return [t for t in (re.sub(r"\s+", " ", unicodedata.normalize("NFKC", unescape(x))).strip() for x in out) if t]


def _label_values(tokens: list[str]) -> dict[str, str]:
    """{label: value text} from "label, value, value…" token runs (dt/dd, th/td, strong/span …)."""
    vals: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i].rstrip(":：")
        if tok in _ALL_LABELS and tok not in vals:
            parts: list[str] = []
            j = i + 1
            while j < len(tokens) and tokens[j].rstrip(":：") not in _ALL_LABELS and len(parts) < 4:
                parts.append(tokens[j])
                j += 1
            vals[tok] = " ".join(parts)
            i = j
            continue
        m = re.match(
            r"^(" + "|".join(sorted(map(re.escape, _ALL_LABELS), key=len, reverse=True)) + r")\s*[:：]\s*(.+)$",
            tokens[i],
        )
        if m and m.group(1) not in vals:
            vals[m.group(1)] = m.group(2)
        i += 1
    return vals


def _pick(vals: dict[str, str], key: str) -> str | None:
    for lab in LABELS[key]:
        if vals.get(lab):
            return vals[lab]
    return None


def _price_man(text: str | None) -> float | None:
    if not text:
        return None
    m = _PRICE.search(text.replace("，", ","))
    if not m:
        return None
    if m.group(3):
        return int(m.group(3)) * 10000.0
    oku = int(m.group(1)) if m.group(1) else 0
    return oku * 10000 + float(m.group(2).replace(",", ""))


def _yen(text: str | None, rent_man: float | None) -> int | None:
    """管理費/敷金/礼金: '5,000円', '8.5万円', '1ヶ月', 'なし', '-'."""
    if not text:
        return None
    t = text.replace(",", "")
    if re.search(r"なし|無|^-$|^－$|^0$", t):
        return 0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ヶ月|か月|カ月|ケ月)", t)
    if m and rent_man:
        return round(float(m.group(1)) * rent_man * 10000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*万\s*円?", t)
    if m:
        return round(float(m.group(1)) * 10000)
    m = re.search(r"(\d+)\s*円", t)
    return int(m.group(1)) if m else None


def _layout(text: str) -> str | None:
    from houseagent.adapters.suumo.adapter import parse_layout

    m = _LAYOUT.search(text.upper())
    return parse_layout(m.group(1).replace(" ", "")) if m else None


def _pref_city(address: str | None, fallback_pref: str | None) -> tuple[str | None, str | None]:
    pref = fallback_pref
    if address:
        for code, ja, _zh, _en in PREFECTURES:
            if address.startswith(ja):
                pref = code
                break
        best: tuple[int, str] | None = None
        for code, pcode, ja, _zh, _en in CITIES:
            if (pref is None or pcode == pref) and ja in address and (best is None or len(ja) > best[0]):
                best = (len(ja), code)
        if best:
            return pref or best[1][:2], best[1]
    return pref, None


def _url_pref(url: str) -> str | None:
    for code, _ja, _zh, en in PREFECTURES:
        if re.search(rf"[/_-]{en.lower()}([/_.-]|$)", url.lower()):
            return code
    return None


def extract_listings(url: str, html: str, allowed_types: list[str]) -> AssistedPage:
    tree = _Tree()
    tree.feed(html)
    host = urlparse(url).netloc.split(":")[0]
    base_domain = ".".join(host.split(".")[-2:]) if host else ""
    # 1. anchors that point at a listing detail page
    for a in tree.anchors:
        href = urljoin(url, unescape(a.attrs.get("href", "")))
        if not href.startswith("http") or (base_domain and base_domain not in urlparse(href).netloc):
            continue
        lid = listing_id(href)
        if lid and _NOT_LISTING.search(urlparse(href).path):
            lid = None
        if lid and not _NAV_WORDS.search(
            urlparse(href).path + ("?" + urlparse(href).query if urlparse(href).query else "")
        ):
            a.attrs["_id"], a.attrs["_href"] = lid, href
            n: _Node | None = a
            while n is not None:
                n.ids.add(lid)
                n = n.parent
    # 2. a card = the highest element holding exactly one listing ID
    cards: dict[str, _Node] = {}
    for a in tree.anchors:
        lid = a.attrs.get("_id")
        if not lid or lid in cards:
            continue
        node = a
        while node.parent is not None and node.parent.ids == {lid}:
            node = node.parent
        cards[lid] = node
    fallback_pref = _url_pref(url)
    deal = TYPE_TO_DEAL.get(allowed_types[0], "buy") if allowed_types else "buy"
    items: list[NormalizedSource] = []
    for lid, card in cards.items():
        tokens = _tokens(card)
        text = " ".join(tokens)
        vals = _label_values(tokens)
        # Rooms of one building often share address / access / age in a building header outside the room card.
        ctx_tokens: list[str] = []
        child, group = card, card.parent
        for _ in range(5):
            # a building header groups a few rooms; a group holding half the page is the result list itself
            if group is None or len(group.ids) > min(30, max(10, len(cards) // 2)):
                break
            ctx_tokens += _own_tokens(group, child)
            if any(t.rstrip(":：") in LABELS["address"] or _looks_address(t) for t in ctx_tokens):
                break
            child, group = group, group.parent
        ctx_vals = _label_values(ctx_tokens)
        for k, v in ctx_vals.items():
            if k not in LABELS["price"]:
                vals.setdefault(k, v)
        price_man = _price_man(_pick(vals, "price") or text)
        if price_man is None or not (_AREA.search(text) or _LAYOUT.search(text.upper())):
            continue  # not a listing card (price alone: ads, agent pages, "recently viewed" links)
        href = next((a.attrs["_href"] for a in _anchors(card) if a.attrs.get("_id") == lid), url)
        titles = [_text(a) for a in _anchors(card) if a.attrs.get("_id") == lid and "#" not in a.attrs["_href"]]
        titles = [t for t in titles if len(t) >= 3 and not _GENERIC_TITLES.match(t)]
        title = (
            _pick(vals, "name")
            or max(titles, key=len, default=None)
            or _heading(card)
            or (_heading(group) if group is not None else None)
        )
        ptype = _type(text + " " + " ".join(ctx_tokens), allowed_types)
        address = _pick(vals, "address") or next((t for t in tokens + ctx_tokens if _looks_address(t)), None)
        if address:
            address = _ADDRESS_NOISE.sub("", address).strip() or None
        pref, city = _pref_city(address, fallback_pref)
        access = _pick(vals, "access") or next(
            (t for t in tokens + ctx_tokens if "駅" in t and ("徒歩" in t or "バス" in t)), ""
        )
        walk = _WALK.search(access) if access and "バス" not in access else None
        st = _STATION.search(access or "")
        land = _area(_pick(vals, "land"))
        building = _area(_pick(vals, "building"))
        exclusive = _area(_pick(vals, "exclusive"))
        area = exclusive or building or (land if ptype == "land" else None) or _area(text)
        built_text = _pick(vals, "built") or ""
        ctx_text = " ".join(ctx_tokens)
        year = _YEAR.search(built_text) or _YEAR.search(text) or _YEAR.search(ctx_text)
        age = _AGE.search(built_text or text) or _AGE.search(ctx_text)
        built_year = (
            int(year.group(1))
            if year and 1900 < int(year.group(1)) <= datetime.now().year
            else (datetime.now().year - int(age.group(1)) if age else None)
        )
        floor_m = _FLOOR.search(_pick(vals, "floor") or "")
        is_rent = TYPE_TO_DEAL.get(ptype or "", deal) == "rent"
        items.append(
            NormalizedSource(
                external_id=lid,
                url=href,
                deal_type="rent" if is_rent else "buy",
                title=(title or "")[:200] or None,
                property_type=ptype,
                prefecture=pref,
                city=city,
                address=address,
                price_yen=round(price_man * 10_000),
                management_fee_yen=_yen(_pick(vals, "fee"), price_man) if is_rent else None,
                deposit_yen=_yen(_pick(vals, "deposit"), price_man) if is_rent else None,
                key_money_yen=_yen(_pick(vals, "key"), price_man) if is_rent else None,
                area_m2=area,
                land_area_m2=land,
                layout=_layout(_pick(vals, "layout") or text) if ptype != "land" else None,
                floor=int(floor_m.group(1)) if floor_m else None,
                built_year=built_year if ptype != "land" else None,
                station=re.split(r"[/／]", st.group(1) or st.group(2))[-1] if st else None,
                walk_minutes=int(walk.group(1)) if walk else None,
                raw_enums={"extractor": "generic"},
            )
        )
    total_m = re.search(r"(?:該当(?:物件)?(?:数|件数)?|検索結果|全)\s*[:：]?\s*([\d,]+)\s*件", _plain(html))
    nxt = _next_link(tree, url)
    # Result pages may carry a CAPTCHA script for their forms; only a page without listings counts as verification.
    verification = len(items) < 2 and looks_like_verification(html) is not None
    return AssistedPage(
        items=items,
        total=int(total_m.group(1).replace(",", "")) if total_m else None,
        next_url=nxt,
        verification=verification,
        is_result_list=len(items) >= 2,
    )


def _looks_address(t: str) -> bool:
    return (
        len(t) < 60
        and "駅" not in t
        and "万円" not in t
        and bool(re.search(r"^\S{2,3}[都道府県]\S+[市区町村郡]|^\S+[市区町村郡]\S*(\d|丁目|大字)", t))
    )


def _heading(n: _Node) -> str | None:
    stack: list[_Node] = [n]
    while stack:
        cur = stack.pop(0)
        if cur.tag in ("h1", "h2", "h3", "h4"):
            t = _text(cur)
            if len(t) >= 3:
                return t
        stack.extend(c for c in cur.children if isinstance(c, _Node) and not (c.ids - n.ids))
    return None


def _own_tokens(group: _Node, exclude: _Node) -> list[str]:
    """Tokens of a group element that are not inside any listing card (the shared building header)."""
    out: list[str] = []
    for c in group.children:
        if isinstance(c, str):
            out.extend(_tokens(_Node("x", {}, children=[c])))
        elif c is not exclude and not c.ids:
            out.extend(_tokens(c))
    return out[:40]


def _anchors(n: _Node) -> list[_Node]:
    out: list[_Node] = []
    stack: list[_Node] = [n]
    while stack:
        cur = stack.pop()
        if cur.tag == "a":
            out.append(cur)
        stack.extend(c for c in cur.children if isinstance(c, _Node))
    return out


def _text(n: _Node) -> str:
    return " ".join(_tokens(n))[:200]


def _area(text: str | None) -> float | None:
    m = _AREA.search(text or "")
    return float(m.group(1)) if m else None


def _type(text: str, allowed: list[str]) -> str | None:
    if len(allowed) == 1:
        return allowed[0]
    text = re.sub(r"土地(面積|権利|権|の権利)", "", text)
    for word, t in TYPE_WORDS:
        if word in text and (not allowed or t in allowed):
            return t
    return allowed[0] if allowed else None


def _plain(html: str) -> str:
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", unescape(re.sub(r"<[^>]+>", " ", t))))


def _next_link(tree: _Tree, url: str) -> str | None:
    current = _current_page(tree.root)
    if current is not None:
        for a in tree.anchors:
            if _text(a).strip() == str(current + 1) and a.attrs.get("href", "").strip() not in ("", "#"):
                return urljoin(url, unescape(a.attrs["href"]))
    for a in tree.anchors:
        label = _text(a)
        rel = a.attrs.get("rel", "")
        if "next" in rel or re.fullmatch(r"(次へ|次のページ|次の\d+件|次|>|›|»|Next)\s*[>›»]?", label or ""):
            href = a.attrs.get("href", "")
            if href and not href.startswith("javascript"):
                return urljoin(url, unescape(href))
    return None


def _current_page(root: _Node) -> int | None:
    """Page number marked as current in a pagination (class *current*/*active* or aria-current)."""
    stack: list[_Node] = [root]
    while stack:
        n = stack.pop()
        cls = n.attrs.get("class", "")
        if n.attrs.get("aria-current") == "page" or re.search(
            r"(^|[\s_-])(current|active|is-current|selected)($|[\s_-])", cls
        ):
            t = _text(n).strip()
            if t.isdigit() and 0 < int(t) < 10000:
                return int(t)
        stack.extend(c for c in n.children if isinstance(c, _Node))
    return None
