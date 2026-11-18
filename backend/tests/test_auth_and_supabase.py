import asyncio
import json
import os
import tempfile

os.environ["DEMO_MODE"] = "true"
os.environ["REFRESH_SECONDS"] = "0"
os.environ["SQLITE_PATH"] = os.path.join(tempfile.gettempdir(), "sharp_auth_test.db")
os.environ["ADMIN_TOKEN"] = ""

import httpx  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.store import SupabaseStore  # noqa: E402


def test_login_flow_and_private_plays(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_LOGIN", True)
    with TestClient(app) as c:
        assert c.get("/api/sharp").status_code == 401
        assert c.get("/api/auth/me").json()["user"] is None
        r = c.post("/api/auth/signup", json={"email": "A@x.com", "password": "short"})
        assert r.status_code == 400
        r = c.post("/api/auth/signup", json={"email": "a@x.com", "password": "password123", "name": "Ann"})
        assert r.status_code == 200
        ta = r.json()["token"]
        ha = {"Authorization": "Bearer " + ta}
        assert c.post("/api/auth/signup", json={"email": "a@x.com", "password": "password123"}).status_code == 409
        assert c.get("/api/sharp", headers=ha).status_code == 200
        assert c.get("/api/auth/me", headers=ha).json()["user"]["name"] == "Ann"
        # private plays
        p = c.post("/api/plays", headers=ha, json={"description": "x", "odds_decimal": 2.0, "stake": 10}).json()
        tb = c.post("/api/auth/signup", json={"email": "b@x.com", "password": "password123"}).json()["token"]
        hb = {"Authorization": "Bearer " + tb}
        assert c.get("/api/plays", headers=hb).json()["plays"] == []
        assert c.delete(f"/api/plays/{p['id']}", headers=hb).status_code == 404
        assert len(c.get("/api/plays", headers=ha).json()["plays"]) == 1
        # wrong password / logout
        assert c.post("/api/auth/login", json={"email": "a@x.com", "password": "nope-nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"email": "a@x.com", "password": "password123"}).status_code == 200
        c.post("/api/auth/logout", headers=ha)
        assert c.get("/api/sharp", headers=ha).status_code == 401
        # password is not stored in clear
        raw = open(settings.SQLITE_PATH, "rb").read()
        assert b"password123" not in raw


def test_admin_email_gates_model_picks(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_LOGIN", True)
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ["boss@x.com"])
    with TestClient(app) as c:
        t = c.post("/api/auth/signup", json={"email": "u@x.com", "password": "password123"}).json()["token"]
        body = [{"away": "BOS", "home": "PHI", "pick": "BOS"}]
        assert c.post("/api/model-picks", json=body, headers={"Authorization": "Bearer " + t}).status_code == 401
        tb = c.post("/api/auth/signup", json={"email": "boss@x.com", "password": "password123"}).json()["token"]
        assert c.post("/api/model-picks", json=body, headers={"Authorization": "Bearer " + tb}).status_code == 200


def _fake_postgrest():
    """Tiny stand-in for Supabase's REST API so SupabaseStore can be tested without a network."""
    db = {"kv": {}, "plays": []}
    api = FastAPI()

    @api.api_route("/rest/v1/{table}", methods=["GET", "POST", "PATCH", "DELETE"])
    async def t(table: str, request: Request):
        q = dict(request.query_params)
        body = await request.body()
        data = json.loads(body) if body else None
        if table == "kv":
            if request.method == "POST":
                db["kv"][data["key"]] = data
                return Response(status_code=201)
            k = q.get("key", "eq.").removeprefix("eq.")
            return [{"value": db["kv"][k]["value"]}] if k in db["kv"] else []
        pid = q.get("id", "").removeprefix("eq.")
        if request.method == "POST":
            row = {"id": f"id{len(db['plays'])}", "created_at": "now", **data}
            db["plays"].append(row)
            return [row]
        if request.method == "PATCH":
            for r in db["plays"]:
                if r["id"] == pid:
                    r.update(data)
                    return [r]
            return []
        if request.method == "DELETE":
            db["plays"] = [r for r in db["plays"] if r["id"] != pid]
            return Response(status_code=204)
        return db["plays"]

    return api, db


def test_supabase_store_against_fake_postgrest():
    api, db = _fake_postgrest()
    st = SupabaseStore("http://fake", "k", transport=httpx.ASGITransport(app=api))

    async def run():
        await st.kv_set("a", {"x": 1})
        assert await st.kv_get("a") == {"x": 1} and await st.kv_get("zzz") is None
        p = await st.play_add({"description": "d", "odds_decimal": 2.0, "stake": 5, "user_id": "u1"})
        assert p["status"] == "open" and p["user_id"] == "u1"
        up = await st.play_update(p["id"], {"status": "won", "pnl": 5, "bogus": 1})
        assert up["status"] == "won" and "bogus" not in up
        assert len(await st.plays_list()) == 1
        await st.play_delete(p["id"])
        assert await st.plays_list() == []
        assert await st.ping() == "ok"

    asyncio.run(run())
