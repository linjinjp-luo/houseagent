"""A local stand-in for AI services in tests: OpenAI Chat Completions and Anthropic Messages wire formats.

It builds answers from the request itself, so assessment output only cites numbers HouseAgent sent - unless the
test switches ``MODE`` to misbehave (invent a rent, return non-JSON, redirect, reject the key).
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

MODE: dict[str, str] = {"value": "ok"}
SEEN: list[dict[str, Any]] = []
GOOD_KEY = "sk-test-GOODKEY-0123456789abcdef"


def _answer(system: str, user: str) -> dict[str, Any]:
    mode = MODE["value"]
    if "Connection test" in system:
        return {"ok": True}
    if "Listing data (JSON)" in user:
        return {"summary": "価格の推移を要約しました。"}
    payload = json.loads(user)
    calcs = payload["calculations"]
    keys = [
        k
        for k in (
            "gross_yield_pct",
            "net_yield_pct",
            "resale_profit_yen",
            "price_vs_comparable_pct",
            "unit_price_yen_m2",
        )
        if k in calcs
    ]
    reasons = [{"text": f"{k}: {calcs[k]['value']}", "refs": [k]} for k in keys[:3]]
    while len(reasons) < 3:
        reasons.append({"text": "条件を確認しました。", "refs": ["price_yen"]})
    if mode == "fabricate":
        reasons[0] = {"text": "周辺の相場家賃は月12.8万円と見込まれます。", "refs": ["price_yen"]}
    return {
        "primary_label": payload["allowed_labels"][0],
        "secondary_labels": payload["rule_tags"][:2],
        "score": payload["rule_score"],
        "confidence": "medium",
        "reasons": reasons,
        "risks": [],
        "next_steps": ["現地を確認"],
    }


def _auth_ok(request: Request) -> bool:
    key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
    return key == GOOD_KEY


def create_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request) -> Any:
        body = await request.json()
        SEEN.append({"api": "anthropic", "headers": dict(request.headers), "body": body})
        if MODE["value"] == "redirect":
            return RedirectResponse("http://127.0.0.1:9/steal", status_code=307)
        if not _auth_ok(request):
            return JSONResponse(
                {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}, status_code=401
            )
        text = (
            "not json"
            if MODE["value"] == "not_json"
            else json.dumps(_answer(body.get("system", ""), body["messages"][0]["content"]), ensure_ascii=False)
        )
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": body["model"] + "-20260901",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 300},
        }

    @app.post("/v1/chat/completions")
    async def openai_chat(request: Request) -> Any:
        body = await request.json()
        SEEN.append({"api": "openai", "headers": dict(request.headers), "body": body})
        if MODE["value"] == "redirect":
            return RedirectResponse("http://127.0.0.1:9/steal", status_code=307)
        if not _auth_ok(request):
            return JSONResponse({"error": {"message": "bad key", "type": "invalid_request_error"}}, status_code=401)
        msgs = body["messages"]
        text = (
            "not json"
            if MODE["value"] == "not_json"
            else json.dumps(_answer(msgs[0]["content"], msgs[1]["content"]), ensure_ascii=False)
        )
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text, "refusal": None},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 250, "total_tokens": 1250},
        }

    return app


def start(port: int) -> uvicorn.Server:
    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_config=None, access_log=False))
    threading.Thread(target=srv.run, daemon=True).start()
    deadline = time.monotonic() + 20
    while not srv.started:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return srv
