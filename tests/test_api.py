from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, MetaData
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from backend.app.config import Settings
from backend.app.database import Base, engine as app_engine, get_db
from backend.app.main import app, limiter
from backend.app.models import User, Transaction, AuthSession
from backend.app.security import hash_password


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    migration_cfg = Config(str(Path(__file__).resolve().parents[1] / "backend" / "alembic.ini"))
    revision = ScriptDirectory.from_config(migration_cfg).get_current_head()
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO alembic_version (version_num) VALUES (?)", (revision,))
    limiter.reset()
    def override_db():
        db = TestingSession()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")
    limiter.reset()


def register(client, email="one@example.com"):
    response = client.post("/api/auth/register", json={"name":"Test User","email":email,"password":"safe-password-123"})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_register_login_and_profile(client):
    headers = register(client)
    assert client.get("/api/auth/me", headers=headers).json()["email"] == "one@example.com"
    assert client.post("/api/auth/register", json={"name":"Duplicate","email":"ONE@example.com","password":"safe-password-123"}).status_code == 409
    result = client.post("/api/auth/login", json={"email":"one@example.com","password":"safe-password-123"})
    assert result.status_code == 200 and result.json()["access_token"]
    assert result.cookies.get("fintrack_refresh")
    assert client.post("/api/auth/login", json={"email":"one@example.com","password":"wrong-password"}).status_code == 401
    assert client.post("/api/auth/register", json={"name":"Long Password","email":"long@example.com","password":"x"*73}).status_code == 422


def test_transaction_crud_and_ownership(client):
    h1 = register(client); h2 = register(client,"two@example.com")
    body={"amount":42.5,"category":"Food","description":"Cafe lunch","kind":"expense","date":"2026-11-02","payment_method":"UPI"}
    made=client.post("/api/transactions",json=body,headers=h1); assert made.status_code==201
    tid=made.json()["id"]
    assert len(client.get("/api/transactions",headers=h1).json())==1
    assert client.get("/api/transactions",headers=h2).json()==[]
    updated={**body,"amount":50}
    assert client.put(f"/api/transactions/{tid}",json=updated,headers=h1).json()["amount"]==50
    assert client.get(f"/api/transactions?kind=income",headers=h1).json()==[]
    assert client.get(f"/api/transactions?search=lunch",headers=h1).json()[0]["id"]==tid
    assert client.delete(f"/api/transactions/{tid}",headers=h2).status_code==404
    assert client.delete(f"/api/transactions/{tid}",headers=h1).status_code==204


def test_budgets_scoped_crud(client):
    h1=register(client); h2=register(client,"two@example.com")
    payload={"category":"Food","month":"2026-11","amount":500}
    item=client.post("/api/budgets",json=payload,headers=h1); assert item.status_code==201
    bid=item.json()["id"]
    assert len(client.get("/api/budgets",headers=h1).json())==1
    assert client.get("/api/budgets",headers=h2).json()==[]
    assert client.put(f"/api/budgets/{bid}",json={**payload,"amount":600},headers=h1).json()["amount"]==600
    assert client.delete(f"/api/budgets/{bid}",headers=h1).status_code==204


def test_categorization_fallback_and_prediction_threshold(client):
    h=register(client)
    result=client.post("/api/categorize",json={"description":"Swiggy order"},headers=h)
    assert result.status_code==200 and result.json()["category"]=="Food"
    assert client.post("/api/categorize",json={"description":""},headers=h).status_code==422
    prediction=client.get("/api/analytics/prediction",headers=h).json()
    assert prediction["available"] is False
    assert "three months" in prediction["reason"]


def test_authentication_required(client):
    assert client.get("/api/transactions").status_code==401
    assert client.get("/api/analytics/dashboard").status_code==401
    assert client.get("/api/transactions", headers={"Authorization":"Bearer invalid-token"}).status_code==401


def test_profile_categories_insights_and_cors(client):
    headers = register(client)
    assert len(client.get("/api/categories", headers=headers).json()) >= 8
    changed = client.patch("/api/auth/me", headers=headers, json={"name":"Updated User"})
    assert changed.status_code == 200 and changed.json()["name"] == "Updated User"
    assert client.get("/api/analytics/insights", headers=headers).json()[0]["type"] == "empty"
    allowed = client.options("/api/auth/me", headers={"Origin":"http://localhost:5173", "Access-Control-Request-Method":"GET"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    denied = client.options("/api/auth/me", headers={"Origin":"https://attacker.example", "Access-Control-Request-Method":"GET"})
    assert denied.headers.get("access-control-allow-origin") is None


def test_database_configuration_and_foreign_keys():
    sqlite_connection = app_engine.connect()
    try:
        assert sqlite_connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        sqlite_connection.close()
    assert Base.metadata.tables["transactions"].c.user_id.foreign_keys
    pg = Settings(database_url="postgresql+psycopg://user:pass@localhost:5432/fintrack")
    pg_engine = create_engine(pg.database_url)
    assert pg_engine.dialect.name == "postgresql"
    assert pg_engine.dialect.driver == "psycopg"
    pg_engine.dispose()
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key="development-only-change-me", frontend_origin="https://fintrack.example")
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key="replace-this-with-a-long-random-secret", frontend_origin="https://fintrack.example", database_url="postgresql+psycopg://user:pass@localhost:5432/fintrack", rate_limit_storage_uri="rediss://localhost:6379/0")
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key="a"*40, frontend_origin="https://fintrack.example", database_url="postgresql+psycopg://user:pass@localhost:5432/fintrack")
    prod = Settings(app_env="production", secret_key="a"*40, frontend_origin="https://fintrack.example", database_url="postgresql+psycopg://user:pass@localhost:5432/fintrack", rate_limit_storage_uri="rediss://localhost:6379/0")
    assert prod.app_env == "production"


def test_sqlite_file_data_persists_after_reconnect():
    db_path = Path(__file__).resolve().parents[1] / "backend" / f"qa-persistence-{uuid4().hex}.db"
    url = f"sqlite:///{db_path.as_posix()}"
    first = create_engine(url, connect_args={"check_same_thread":False})
    second = None
    try:
        Base.metadata.create_all(first)
        with sessionmaker(bind=first)() as session:
            session.add(User(name="Persisted", email="persist@example.com", password_hash=hash_password("test-password-123")))
            session.commit()
        first.dispose()
        second = create_engine(url, connect_args={"check_same_thread":False})
        with sessionmaker(bind=second)() as session:
            assert session.query(User).filter_by(email="persist@example.com").one().name == "Persisted"
    finally:
        first.dispose()
        if second:
            second.dispose()
        db_path.unlink(missing_ok=True)


def test_personalized_expense_classifier(client):
    headers = register(client)
    samples = [
        ("Food", "restaurant pizza delivery"), ("Food", "burger takeaway lunch"),
        ("Food", "pasta dinner restaurant"), ("Food", "sushi food order"),
        ("Transportation", "taxi commute ride"), ("Transportation", "metro transit commute"),
        ("Transportation", "cab ride city"), ("Transportation", "bus transit trip"),
    ]
    for category, description in samples:
        response = client.post("/api/transactions", headers=headers, json={"amount":10,"category":category,"description":description,"kind":"expense","date":date.today().isoformat(),"payment_method":"Card"})
        assert response.status_code == 201
    suggestion = client.post("/api/categorize", headers=headers, json={"description":"pizza restaurant takeaway"})
    assert suggestion.status_code == 200
    assert suggestion.json()["method"] == "personalized classifier"
    assert suggestion.json()["category"] == "Food"


def test_auth_refresh_rotation_replay_and_logout(client):
    created = client.post("/api/auth/register", json={"name":"Session User","email":"session@example.com","password":"safe-password-123"})
    assert created.status_code == 201
    initial = created.json()
    assert initial["expires_in"] == 15 * 60
    assert "refresh_token" not in initial
    old_refresh = created.cookies.get("fintrack_refresh")
    assert old_refresh
    set_cookie = created.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "path=/api/auth" in set_cookie
    old_headers = {"Authorization":f"Bearer {initial['access_token']}"}
    assert client.get("/api/auth/me", headers=old_headers).status_code == 200

    rotated = client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    current = rotated.json()
    assert "refresh_token" not in current
    current_refresh = rotated.cookies.get("fintrack_refresh")
    assert current_refresh and current_refresh != old_refresh
    assert client.get("/api/auth/me", headers={"Authorization":f"Bearer {current['access_token']}"}).status_code == 200

    # Reuse of a rotated refresh token is detected and revokes that session.
    with TestClient(app) as replay_client:
        replay_client.cookies.set("fintrack_refresh", old_refresh, path="/api/auth")
        replay = replay_client.post("/api/auth/refresh")
        assert replay.status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization":f"Bearer {current['access_token']}"}).status_code == 401

    second = client.post("/api/auth/login", json={"email":"session@example.com","password":"safe-password-123"}).json()
    assert "refresh_token" not in second
    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 204
    assert "max-age=0" in logout_response.headers["set-cookie"].lower()
    assert client.get("/api/auth/me", headers={"Authorization":f"Bearer {second['access_token']}"}).status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401


def test_auth_rate_limit_and_health_monitoring(client):
    statuses = [client.post("/api/auth/login", json={"email":"missing@example.com","password":"bad-password"}).status_code for _ in range(6)]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
    live = client.get("/api/health/live")
    ready = client.get("/api/health/ready")
    metrics = client.get("/metrics")
    assert live.status_code == 200 and live.json()["status"] == "ok"
    assert ready.status_code == 200 and ready.json()["database"] == "ok"
    assert ready.json()["migrations"] == "current"
    assert live.headers.get("x-request-id")
    assert metrics.status_code == 200 and "http_requests_total" in metrics.text


def test_readiness_returns_503_when_database_query_fails(client, monkeypatch, caplog):
    from sqlalchemy.orm import Session
    def fail_execute(self, *args, **kwargs):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(Session, "execute", fail_execute)
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert "readiness database check failed" in caplog.text


def test_alembic_fresh_install_and_legacy_adoption():
    backend_dir = Path(__file__).resolve().parents[1] / "backend"
    cfg_path = backend_dir / "alembic.ini"

    def config_for(db_path):
        cfg = Config(str(cfg_path))
        cfg.attributes["database_url"] = f"sqlite:///{db_path.as_posix()}"
        return cfg

    fresh_path = backend_dir / f"qa-migration-fresh-{uuid4().hex}.db"
    legacy_path = backend_dir / f"qa-migration-legacy-{uuid4().hex}.db"
    engines = []
    try:
        command.upgrade(config_for(fresh_path), "head")
        fresh = create_engine(f"sqlite:///{fresh_path.as_posix()}"); engines.append(fresh)
        assert {"users","categories","transactions","budgets","financial_insights","savings_goals","auth_sessions","alembic_version"}.issubset(set(inspect(fresh).get_table_names()))
        command.downgrade(config_for(fresh_path), "base")
        assert "auth_sessions" not in inspect(fresh).get_table_names()
        assert "users" in inspect(fresh).get_table_names()
        command.upgrade(config_for(fresh_path), "head")

        legacy = create_engine(f"sqlite:///{legacy_path.as_posix()}"); engines.append(legacy)
        legacy_metadata = MetaData()
        for name, table in Base.metadata.tables.items():
            if name != "auth_sessions": table.to_metadata(legacy_metadata)
        legacy_metadata.create_all(legacy)
        users_table = legacy_metadata.tables["users"]
        with legacy.begin() as connection:
            connection.execute(users_table.insert().values(name="Existing user", email="keep@example.com", password_hash=hash_password("safe-password-123"), created_at=datetime.now(timezone.utc)))
        command.upgrade(config_for(legacy_path), "head")
        assert "auth_sessions" in inspect(legacy).get_table_names()
        with legacy.connect() as connection:
            assert connection.execute(users_table.select().where(users_table.c.email=="keep@example.com")).scalar_one_or_none() is not None
    finally:
        for db_engine in engines: db_engine.dispose()
        fresh_path.unlink(missing_ok=True); legacy_path.unlink(missing_ok=True)


def test_goals_dashboard_forecast_and_csv(client):
    h1 = register(client)
    h2 = register(client, "two@example.com")
    private_payload = {"amount":999,"category":"Other","description":"PRIVATE OTHER USER","kind":"expense","date":date.today().isoformat(),"payment_method":"Card"}
    assert client.post("/api/transactions", headers=h2, json=private_payload).status_code == 201
    goal = client.post("/api/goals", headers=h1, json={"name":"Emergency fund","target_amount":1000,"saved_amount":125})
    assert goal.status_code == 201
    goal_id = goal.json()["id"]
    assert client.get("/api/goals", headers=h1).json()[0]["saved_amount"] == 125
    assert client.get("/api/goals", headers=h2).json() == []

    # Current month affects dashboard summary; older months exercise historical forecast.
    now = date.today()
    # Compute calendar months by offsets to handle year boundaries.
    from datetime import date as date_type
    months = [(now.year + (now.month - offset - 1) // 12, (now.month - offset - 1) % 12 + 1) for offset in (2, 1, 0)]
    amounts = [100, 120, 140]
    for (year, month), amount in zip(months, amounts):
        payload = {"amount": amount, "category":"Food", "description":f"Month {month}", "kind":"expense", "date":date_type(year, month, 10).isoformat(), "payment_method":"Card"}
        assert client.post("/api/transactions", headers=h1, json=payload).status_code == 201
    current_budget = client.post("/api/budgets", headers=h1, json={"category":"Food","month":now.strftime("%Y-%m"),"amount":200})
    assert current_budget.status_code == 201
    overall_budget = client.post("/api/budgets", headers=h1, json={"category":"All categories","month":now.strftime("%Y-%m"),"amount":1000})
    assert overall_budget.status_code == 201
    dashboard = client.get("/api/analytics/dashboard", headers=h1).json()
    assert dashboard["expenses"] == 140
    assert dashboard["categories"] == [{"name":"Food", "amount":140}]
    assert dashboard["budget"] == 1000 and dashboard["budget_used"] == 140
    insights = client.get("/api/analytics/insights", headers=h1).json()
    assert any("Food is your largest expense" in item["title"] for item in insights)
    forecast = client.get("/api/analytics/prediction", headers=h1).json()
    assert forecast["available"] is True
    assert forecast["previous"] == 140
    assert forecast["predicted"] > 0

    csv_response = client.get("/api/reports/export.csv", headers=h1)
    assert csv_response.status_code == 200
    assert "Month" in csv_response.text and "PRIVATE OTHER USER" not in csv_response.text
    assert client.delete(f"/api/goals/{goal_id}", headers=h2).status_code == 404
    assert client.delete(f"/api/goals/{goal_id}", headers=h1).status_code == 204


def test_transaction_validation_and_date_filter(client):
    headers = register(client)
    bad = {"amount":-1,"category":"Food","description":"Invalid","kind":"expense","date":"not-a-date"}
    assert client.post("/api/transactions", headers=headers, json=bad).status_code == 422
    non_finite = '{"amount": Infinity, "category":"Food", "description":"Invalid", "kind":"expense", "date":"2026-11-02"}'
    assert client.post("/api/transactions", headers={**headers,"Content-Type":"application/json"}, content=non_finite).status_code == 422
    payload = {"amount":10,"category":"Food","description":"Lunch","kind":"expense","date":"2026-11-02","payment_method":"UPI"}
    assert client.post("/api/transactions", headers=headers, json=payload).status_code == 201
    assert len(client.get("/api/transactions?start=2026-11-01&end=2026-11-03", headers=headers).json()) == 1
    assert client.get("/api/transactions?start=2026-12-01", headers=headers).json() == []


def test_csv_escapes_spreadsheet_formulas(client):
    headers = register(client)
    payload = {"amount":10,"category":"Food","description":"=HYPERLINK(\"https://attacker.invalid\")","kind":"expense","date":date.today().isoformat(),"payment_method":"Card"}
    assert client.post("/api/transactions", headers=headers, json=payload).status_code == 201
    report = client.get("/api/reports/export.csv", headers=headers)
    assert report.status_code == 200
    assert "'=HYPERLINK" in report.text
