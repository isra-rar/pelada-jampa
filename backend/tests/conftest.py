import os
import tempfile
import warnings

os.environ["PELADA_DATABASE_URL"] = os.environ.get("PELADA_TEST_DATABASE_URL", "sqlite://")
os.environ["PELADA_SCHEDULER_ENABLED"] = "false"
os.environ["PELADA_ENVIRONMENT"] = "test"
os.environ["PELADA_DRAW_TIME_LIMIT_SECONDS"] = "0.5"
os.environ["PELADA_ACCESS_TOKEN_EXPIRE_MINUTES"] = str(60 * 24 * 30)
os.environ["PELADA_MEDIA_DIR"] = tempfile.mkdtemp(prefix="pelada-media-")
os.environ.pop("PELADA_VAPID_PUBLIC_KEY", None)
os.environ.pop("PELADA_VAPID_PRIVATE_KEY", None)
warnings.filterwarnings("ignore", category=DeprecationWarning)

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402,F401
from app.core import db as dbmod  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app as api  # noqa: E402
from app.services.groups import seed_default_skills  # noqa: E402

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed_default_skills(s)
    dbmod.set_now(T0)
    yield
    dbmod.set_now(None)


@pytest.fixture
def clock():
    class Clock:
        def set(self, dt: datetime):
            dbmod.set_now(dt)

        def advance(self, **kw):
            dbmod.set_now(dbmod.utcnow() + timedelta(**kw))

        @property
        def now(self):
            return dbmod.utcnow()

    return Clock()


@pytest.fixture
def client():
    with TestClient(api) as c:
        yield c


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


class Api:
    def __init__(self, client: TestClient):
        self.c = client

    def user(self, name: str) -> dict:
        email = f"{name.lower()}@example.com"
        r = self.c.post("/api/v1/auth/register", json={"email": email, "name": name, "password": "senha-segura"})
        assert r.status_code == 201, r.text
        r = self.c.post("/api/v1/auth/login", json={"email": email, "password": "senha-segura"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}", "_email": email, "_name": name}

    def call(self, method: str, url: str, headers: dict, expected: int | None = 200, **kw):
        h = {k: v for k, v in headers.items() if not k.startswith("_")}
        r = self.c.request(method, f"/api/v1{url}", headers=h, **kw)
        if expected is not None:
            assert r.status_code == expected, f"{method} {url} -> {r.status_code}: {r.text}"
        return r.json() if r.content else None

    def get(self, url, headers, expected=200, **kw):
        return self.call("GET", url, headers, expected, **kw)

    def post(self, url, headers, expected=200, **kw):
        return self.call("POST", url, headers, expected, **kw)

    def patch(self, url, headers, expected=200, **kw):
        return self.call("PATCH", url, headers, expected, **kw)

    def put(self, url, headers, expected=200, **kw):
        return self.call("PUT", url, headers, expected, **kw)


@pytest.fixture
def apiclient(client):
    return Api(client)


def match_payload(**overrides) -> dict:
    starts = T0 + timedelta(days=2)
    data = {
        "name": "Pelada de quinta",
        "location": "Arena Society",
        "starts_at": starts.isoformat(),
        "duration_minutes": 90,
        "price": "20.00",
        "slots": 4,
        "field_players_per_team": 2,
        "goalkeepers_per_team": 0,
        "evaluation_window_hours": 6,
    }
    data.update(overrides)
    return data
