"""Local mock property websites (``/mock-site/a`` and ``/mock-site/b``).

Covers the nine acceptance scenarios of spec 15.8: login success, login expired, multi-page results, no
results, price change, duplicate listings, page timeout, page-structure change and manual action (captcha).
State is in memory and resets when the app restarts.
"""

from __future__ import annotations

import html
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from houseagent.mock_site.data import MockProperty, generate

SCENARIOS = (
    "normal",
    "login_expired",
    "no_results",
    "timeout",
    "structure_changed",
    "captcha",
    "rate_limited",
    "duplicates",
)
PER_PAGE = 20
SESSION_TTL = timedelta(hours=12)


class VariantState:
    def __init__(self, variant: str) -> None:
        self.variant = variant
        self.name = "モック不動産A" if variant == "a" else "モック不動産B"
        self.reset()

    def reset(self) -> None:
        self.props: dict[str, MockProperty] = {p.id: p for p in generate(self.variant)}
        self.sessions: dict[str, datetime] = {}
        self.scenario = "normal"
        self.delay_s = 5.0
        self.next_order = max(p.listed_order for p in self.props.values()) + 1

    @property
    def cookie_name(self) -> str:
        return f"mock_session_{self.variant}"

    def logged_in(self, request: Request) -> bool:
        sid = request.cookies.get(self.cookie_name)
        exp = self.sessions.get(sid or "")
        return bool(exp and exp > datetime.now(UTC) and self.scenario != "login_expired")


STATES = {"a": VariantState("a"), "b": VariantState("b")}


def _page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        "<style>body{font-family:sans-serif;max-width:960px;margin:24px auto;padding:0 16px}"
        "li{margin:8px 0;padding:8px;border:1px solid #ddd;border-radius:6px;list-style:none}"
        ".ha-price{font-weight:bold;color:#b00}</style></head>"
        f"<body><p><a href='/mock-site/'>Mock sites</a> · テスト用の架空サイト (not a real website)</p>"
        f"{body}</body></html>"
    )


def _filter(st: VariantState, q: dict[str, str]) -> list[MockProperty]:
    items = [p for p in st.props.values()]
    types = [t for t in q.get("type", "").split(",") if t]
    if types:
        items = [p for p in items if p.property_type in types]
    prefs = [t for t in q.get("pref", "").split(",") if t]
    if prefs:
        items = [p for p in items if p.prefecture in prefs]
    if st.variant == "a":  # site B supports fewer parameters (the adapter filters locally)
        cities = [t for t in q.get("city", "").split(",") if t]
        if cities:
            items = [p for p in items if p.city in cities]
        if q.get("amin"):
            items = [p for p in items if p.area_m2 >= float(q["amin"])]
        if q.get("amax"):
            items = [p for p in items if p.area_m2 <= float(q["amax"])]
        layouts = [t for t in q.get("layout", "").split(",") if t]
        if layouts:
            items = [p for p in items if p.layout in layouts]
        if q.get("age"):
            min_year = datetime.now().year - int(q["age"])
            items = [p for p in items if p.property_type == "land" or p.built_year >= min_year]
        if q.get("kw"):
            kws = [k for k in q["kw"].split(",") if k]
            items = [p for p in items if all(k in (p.title + p.address) for k in kws)]
    if q.get("pmin"):
        items = [p for p in items if p.price_man >= float(q["pmin"])]
    if q.get("pmax"):
        items = [p for p in items if p.price_man <= float(q["pmax"])]
    items.sort(key=lambda p: -p.listed_order)
    return items


def _item_html(st: VariantState, p: MockProperty) -> str:
    def span(cls: str, val: Any) -> str:
        return f"<span class='{cls}'>{html.escape('' if val is None else str(val))}</span>"

    return (
        f"<li class='ha-item' data-id='{p.id}'>"
        f"<a class='ha-link' href='/mock-site/{st.variant}/property/{p.id}'>{html.escape(p.title)}</a><br>"
        + span("ha-type", p.property_type)
        + span("ha-pref", p.prefecture)
        + span("ha-city", p.city)
        + " "
        + span("ha-address", p.address)
        + " "
        + span("ha-building", p.building_name)
        + "<br>価格 "
        + span("ha-price", p.price_man)
        + "万円 · 面積 "
        + span("ha-area", p.area_m2)
        + "㎡"
        + " · 土地 "
        + span("ha-land", p.land_area_m2)
        + " · "
        + span("ha-layout", p.layout)
        + " · "
        + span("ha-floor", p.floor)
        + "階 · 築 "
        + span("ha-built", p.built_year)
        + " · "
        + span("ha-station", p.station)
        + " 徒歩"
        + span("ha-walk", p.walk_minutes)
        + "分"
        + " · 管理費 "
        + span("ha-mgmt", p.management_fee_yen)
        + " · 敷金 "
        + span("ha-deposit", p.deposit_yen)
        + " · 礼金 "
        + span("ha-key", p.key_money_yen)
        + " · "
        + span("ha-status", "available" if p.available else "ended")
        + "</li>"
    )


def create_mock_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        rows = []
        for v, st in STATES.items():
            opts = "".join(f"<option {'selected' if s == st.scenario else ''}>{s}</option>" for s in SCENARIOS)
            rows.append(
                f"<h2>{st.name} (/{v})</h2><p>物件数 {len(st.props)} · シナリオ <b>{st.scenario}</b> · "
                f"<a href='/mock-site/{v}/login'>ログイン</a> · <a href='/mock-site/{v}/search'>検索</a></p>"
                f"<form method='post' action='/mock-site/{v}/admin/scenario'><select name='scenario'>{opts}"
                f"</select><button>シナリオ変更</button></form>"
                f"<form method='post' action='/mock-site/{v}/admin/simulate'><button>市場変化をシミュレート"
                f"（値下げ・新着・掲載終了）</button></form>"
                f"<form method='post' action='/mock-site/{v}/admin/reset'><button>初期化</button></form>"
            )
        return _page("Mock sites", "<h1>HouseAgent モックサイト</h1>" + "".join(rows))

    @app.get("/{variant}/login", response_class=HTMLResponse)
    def login_form(variant: str) -> str:
        st = STATES[variant]
        return _page(
            f"{st.name} ログイン",
            f"<h1>{st.name} 会員ログイン</h1><form id='login-form' method='post' "
            f"action='/mock-site/{variant}/login'><label>ID <input name='username'></label> "
            "<label>パスワード <input name='password' type='password'></label> "
            "<button>ログイン</button></form><p>任意のID・パスワードでログインできます。</p>",
        )

    @app.post("/{variant}/login")
    def login(variant: str, username: str = Form(""), password: str = Form("")) -> Response:
        st = STATES[variant]
        if not username or not password:
            return HTMLResponse(_page("error", "<p>IDとパスワードを入力してください</p>"), status_code=400)
        sid = secrets.token_urlsafe(16)
        st.sessions[sid] = datetime.now(UTC) + SESSION_TTL
        if st.scenario == "login_expired":
            st.scenario = "normal"
        resp = RedirectResponse(f"/mock-site/{variant}/mypage", status_code=303)
        resp.set_cookie(
            st.cookie_name,
            sid,
            httponly=True,
            samesite="lax",
            path="/mock-site/",
            max_age=int(SESSION_TTL.total_seconds()),  # persistent, like real sites
        )
        return resp

    @app.get("/{variant}/mypage", response_class=HTMLResponse)
    def mypage(variant: str, request: Request) -> str:
        st = STATES[variant]
        state = "ログイン中" if st.logged_in(request) else "未ログイン"
        return _page(
            "マイページ",
            f"<h1>{st.name} マイページ</h1><p id='login-state'>{state}</p>"
            "<p>このウィンドウを閉じて HouseAgent で「ログイン状態を確認」を押してください。</p>",
        )

    @app.get("/{variant}/api/session")
    def session_state(variant: str, request: Request) -> JSONResponse:
        return JSONResponse({"logged_in": STATES[variant].logged_in(request)})

    @app.get("/{variant}/robots.txt", response_class=PlainTextResponse)
    def robots(variant: str) -> str:
        """Fictional rules used to exercise the automatic rules check."""
        lines = ["User-agent: *", f"Disallow: /mock-site/{variant}/search", "Allow: /", ""]
        return chr(10).join(lines)

    @app.get("/{variant}/terms", response_class=HTMLResponse)
    def terms(variant: str) -> str:
        return _page(
            "利用規約",
            "<h1>利用規約（架空）</h1><p>第3条 禁止事項 (1) 当サイトの情報をスクレイピング等の"
            "自動的な手段で収集する行為 (2) 商業目的で利用する行為</p>"
            "<p>第4条 コンテンツは私的利用の範囲でご利用ください。</p>",
        )

    @app.get("/{variant}/search", response_model=None)
    def search(variant: str, request: Request) -> Response:
        st = STATES[variant]
        q = dict(request.query_params)
        if st.scenario == "rate_limited":
            return HTMLResponse(_page("429", "<h1>アクセスが集中しています</h1>"), status_code=429)
        if st.scenario == "timeout":
            time.sleep(st.delay_s)
        if q.get("public") != "1" and not st.logged_in(request):  # public=1: search without login
            return HTMLResponse(login_form(variant))
        if st.scenario == "captcha":
            return HTMLResponse(
                _page(
                    "確認",
                    "<h1>ロボットではないことを確認してください</h1><form id='captcha'><input name='answer'></form>",
                )
            )
        items = [] if st.scenario == "no_results" else _filter(st, q)
        page = max(int(q.get("page", "1")), 1)
        per_page = int(q.get("per_page", PER_PAGE))
        start = (page - 1) * per_page
        chunk = items[start : start + per_page]
        if st.scenario == "duplicates" and page > 1:
            chunk = items[max(start - 2, 0) : start] + chunk  # previous page's last two appear again
        has_next = start + per_page < len(items)
        if st.scenario == "structure_changed":
            cards = "".join(f"<div class='card-v2' data-key='{p.id}'>{html.escape(p.title)}</div>" for p in chunk)
            return HTMLResponse(_page("検索結果", f"<main class='result-v2'>{cards}</main>"))
        lis = "".join(_item_html(st, p) for p in chunk)
        nxt = ""
        if has_next:
            params = {**q, "page": str(page + 1)}
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            nxt = f"<a class='ha-next' href='/mock-site/{variant}/search?{qs}'>次へ</a>"
        empty = "<p class='ha-empty'>該当する物件はありません</p>" if not chunk else ""
        body = (
            f"<h1>{st.name} 検索結果</h1><div id='ha-count' data-total='{len(items)}'>{len(items)}件</div>"
            f"<ul id='results'>{lis}</ul>{empty}{nxt}"
        )
        return HTMLResponse(_page("検索結果", body))

    @app.get("/{variant}/property/{pid}", response_class=HTMLResponse)
    def detail(variant: str, pid: str) -> Response:
        st = STATES[variant]
        p = st.props.get(pid)
        if p is None:
            return HTMLResponse(_page("404", "<h1>ページが見つかりません</h1>"), status_code=404)
        status = "販売中" if p.available else "<b>掲載終了</b>"
        body = (
            f"<h1>{html.escape(p.title)}</h1><p>{status}</p><ul>{_item_html(st, p)}</ul>"
            "<p>（架空の物件詳細ページ。写真・間取り図・詳細説明は元サイトでのみ提供されます）</p>"
        )
        return HTMLResponse(_page(p.title, body))

    # ---- admin controls (for demos and automated tests) -----------------------------------------------

    def _done(request: Request, payload: dict[str, Any]) -> Response:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(payload)
        return RedirectResponse("/mock-site/", status_code=303)

    @app.post("/{variant}/admin/scenario")
    async def set_scenario(variant: str, request: Request) -> Response:
        st = STATES[variant]
        data = await _body(request)
        scenario = str(data.get("scenario", "normal"))
        if scenario not in SCENARIOS:
            return JSONResponse({"error": "unknown scenario"}, status_code=400)
        st.scenario = scenario
        if "delay_s" in data:
            st.delay_s = float(data["delay_s"])
        return _done(request, {"scenario": st.scenario})

    @app.post("/{variant}/admin/price")
    async def set_price(variant: str, request: Request) -> Response:
        data = await _body(request)
        p = STATES[variant].props[str(data["id"])]
        p.price_man = float(data["price_man"])
        return _done(request, p.to_dict())

    @app.post("/{variant}/admin/availability")
    async def set_availability(variant: str, request: Request) -> Response:
        data = await _body(request)
        p = STATES[variant].props[str(data["id"])]
        p.available = str(data.get("available", "true")).lower() in {"1", "true", "yes"}
        return _done(request, p.to_dict())

    @app.post("/{variant}/admin/remove")
    async def remove(variant: str, request: Request) -> Response:
        data = await _body(request)
        STATES[variant].props.pop(str(data["id"]), None)
        return _done(request, {"removed": data["id"]})

    @app.post("/{variant}/admin/simulate")
    def simulate(variant: str, request: Request) -> Response:
        import random

        st = STATES[variant]
        rng = random.Random()
        props = [p for p in st.props.values() if p.available]
        for p in rng.sample(props, min(3, len(props))):
            p.price_man = int(p.price_man * rng.choice([0.93, 0.95, 0.97, 1.03]))
        if props:
            rng.choice(props).available = False
        from houseagent.mock_site.data import _make

        new_id = f"{'A' if variant == 'a' else 'B-'}{7000 + st.next_order}"
        np = _make(rng, new_id, st.next_order)
        st.next_order += 1
        st.props[np.id] = np
        return _done(request, {"simulated": True, "new_id": np.id})

    @app.post("/{variant}/admin/reset")
    def reset(variant: str, request: Request) -> Response:
        STATES[variant].reset()
        return _done(request, {"reset": True})

    return app


async def _body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").startswith("application/json"):
        return dict(await request.json())
    form = await request.form()
    return {k: v for k, v in form.items()}
