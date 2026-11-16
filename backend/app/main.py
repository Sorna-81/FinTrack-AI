from collections import defaultdict
from datetime import date
import csv, io
import math
import logging
import time
from uuid import uuid4
from fastapi import FastAPI, Depends, HTTPException, Query, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from prometheus_fastapi_instrumentator import Instrumentator
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pathlib import Path
from redis import Redis
from .config import settings
from .database import get_db
from .models import User, Transaction, Budget, Category, SavingsGoal
from .schemas import Register, Login, TransactionIn, TransactionOut, BudgetIn, GoalIn
from .security import current_user, hash_password, verify_password, create_session, rotate_refresh_token, revoke_refresh_token
from .ml import categorize, prediction

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("fintrack.api")
app = FastAPI(title="FinTrack AI API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
limiter = Limiter(key_func=get_remote_address, storage_uri=settings.rate_limit_storage_uri, default_limits=["300/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
REFRESH_COOKIE = "fintrack_refresh"

def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite=settings.auth_cookie_samesite,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        path="/api/auth",
    )

@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed request_id=%s method=%s path=%s", request_id, request.method, request.url.path)
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info("request complete request_id=%s method=%s path=%s status=%s duration_ms=%.1f", request_id, request.method, request.url.path, response.status_code, duration_ms)
    return response

def sanitize_error_values(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: sanitize_error_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_error_values(item) for item in value]
    return value

@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(sanitize_error_values(exc.errors()))})

@app.get("/api/health")
def health(): return {"status": "ok"}

@app.get("/api/health/live", include_in_schema=False)
def health_live(): return {"status": "ok"}

@app.get("/api/health/ready", include_in_schema=False)
def health_ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        alembic_config = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        expected_revision = ScriptDirectory.from_config(alembic_config).get_current_head()
        current_revision = MigrationContext.configure(db.connection()).get_current_revision()
        if current_revision != expected_revision:
            raise HTTPException(503, "Database migrations are not current")
        if settings.rate_limit_storage_uri.startswith(("redis://", "rediss://")):
            redis_client = Redis.from_url(settings.rate_limit_storage_uri, socket_connect_timeout=2, socket_timeout=2)
            try:
                redis_client.ping()
            finally:
                redis_client.close()
            limiter_status = "ok"
        else:
            limiter_status = "local-memory"
    except Exception as exc:
        logger.exception("readiness database check failed")
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(503, "Database is unavailable") from exc
    return {"status": "ready", "database": "ok", "migrations": "current", "rate_limiter": limiter_status}

@app.post("/api/auth/register", status_code=201)
@limiter.limit("5/minute")
def register(request: Request, response: Response, data: Register, db: Session = Depends(get_db)):
    if db.query(User).filter(func.lower(User.email) == data.email.lower()).first(): raise HTTPException(409, "Email already registered")
    try:
        user = User(name=data.name, email=data.email.lower(), password_hash=hash_password(data.password)); db.add(user); db.flush()
        for name, color in [("Food", "#f59e0b"), ("Housing", "#6366f1"), ("Transportation", "#06b6d4"), ("Shopping", "#ec4899"), ("Utilities", "#8b5cf6"), ("Health", "#10b981"), ("Entertainment", "#f97316"), ("Other", "#64748b"), ("Salary", "#22c55e")]: db.add(Category(user_id=user.id, name=name, color=color, kind="income" if name == "Salary" else "expense"))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Email already registered")
    tokens = create_session(db, user)
    set_refresh_cookie(response, tokens.pop("refresh_token"))
    return tokens

@app.post("/api/auth/login")
@limiter.limit("5/minute")
def login(request: Request, response: Response, data: Login, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == data.email.lower()).first()
    if not user or not verify_password(data.password, user.password_hash): raise HTTPException(401, "Incorrect email or password")
    tokens = create_session(db, user)
    set_refresh_cookie(response, tokens.pop("refresh_token"))
    return tokens

@app.post("/api/auth/refresh")
@limiter.limit("20/minute")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(401, "Refresh session cookie is required")
    tokens = rotate_refresh_token(db, token)
    set_refresh_cookie(response, tokens.pop("refresh_token"))
    return tokens

@app.post("/api/auth/logout", status_code=204)
@limiter.limit("20/minute")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        revoke_refresh_token(db, token)
    response.delete_cookie(key=REFRESH_COOKIE, path="/api/auth", secure=settings.app_env == "production", httponly=True, samesite=settings.auth_cookie_samesite)
    response.status_code = 204
    return response

@app.get("/api/auth/me")
def me(user: User = Depends(current_user)): return {"id": user.id, "name": user.name, "email": user.email}

@app.patch("/api/auth/me")
def update_me(data: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if "name" in data and isinstance(data["name"], str) and data["name"].strip(): user.name = data["name"].strip()[:120]
    db.commit(); return {"id": user.id, "name": user.name, "email": user.email}

@app.get("/api/categories")
def categories(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "kind": c.kind, "color": c.color} for c in db.query(Category).filter_by(user_id=user.id).all()]

@app.get("/api/transactions", response_model=list[TransactionOut])
def list_transactions(search: str = "", category: str = "", kind: str = "", start: date | None = None, end: date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Transaction).filter(Transaction.user_id == user.id)
    if search: q = q.filter(Transaction.description.ilike(f"%{search}%"))
    if category: q = q.filter(Transaction.category == category)
    if kind in ("income", "expense"): q = q.filter(Transaction.kind == kind)
    if start: q = q.filter(Transaction.date >= start)
    if end: q = q.filter(Transaction.date <= end)
    return q.order_by(Transaction.date.desc(), Transaction.id.desc()).all()

@app.post("/api/transactions", response_model=TransactionOut, status_code=201)
def add_transaction(data: TransactionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = Transaction(**data.model_dump(), user_id=user.id); db.add(row); db.commit(); db.refresh(row); return row

def owned_transaction(tid: int, uid: int, db: Session):
    row = db.query(Transaction).filter_by(id=tid, user_id=uid).first()
    if not row: raise HTTPException(404, "Transaction not found")
    return row

@app.put("/api/transactions/{tid}", response_model=TransactionOut)
def edit_transaction(tid: int, data: TransactionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = owned_transaction(tid, user.id, db)
    for key, value in data.model_dump().items(): setattr(row, key, value)
    db.commit(); db.refresh(row); return row

@app.delete("/api/transactions/{tid}", status_code=204)
def delete_transaction(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(owned_transaction(tid, user.id, db)); db.commit(); return Response(status_code=204)

@app.post("/api/categorize")
def classify(data: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    description = str(data.get("description", ""))[:500]
    if not description.strip(): raise HTTPException(422, "Description is required")
    return categorize(description, db, user.id)

@app.get("/api/analytics/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Transaction).filter_by(user_id=user.id).all(); today = date.today(); month = today.strftime("%Y-%m")
    inc = sum(t.amount for t in rows if t.kind == "income" and t.date.strftime("%Y-%m") == month)
    exp = sum(t.amount for t in rows if t.kind == "expense" and t.date.strftime("%Y-%m") == month)
    cat, trends = defaultdict(float), defaultdict(lambda: {"income": 0.0, "expense": 0.0})
    for t in rows:
        m = t.date.strftime("%Y-%m"); trends[m][t.kind] += t.amount
        if t.kind == "expense" and m == month: cat[t.category] += t.amount
    budgets = db.query(Budget).filter_by(user_id=user.id, month=month).all()
    overall_budget = sum(b.amount for b in budgets if b.category == "All categories")
    if overall_budget:
        budget_total = overall_budget
        budget_used = exp
    else:
        budget_total = sum(b.amount for b in budgets)
        budget_used = sum(sum(t.amount for t in rows if t.kind == "expense" and t.date.strftime("%Y-%m") == month and t.category == b.category) for b in budgets)
    return {"month": month, "income": round(inc, 2), "expenses": round(exp, 2), "balance": round(sum(t.amount if t.kind == "income" else -t.amount for t in rows), 2), "savings": round(inc-exp, 2), "budget": budget_total, "budget_used": round(budget_used, 2), "categories": [{"name": k, "amount": round(v, 2)} for k,v in sorted(cat.items(), key=lambda x:-x[1])], "trends": [{"month": k, **{a: round(b[a],2) for a in ("income","expense")}} for k,b in sorted(trends.items())[-6:]], "recent": [{"id": t.id, "amount": t.amount, "category": t.category, "description": t.description, "kind": t.kind, "date": t.date.isoformat(), "payment_method": t.payment_method} for t in sorted(rows,key=lambda x:(x.date,x.id),reverse=True)[:6]]}

@app.get("/api/analytics/insights")
def insights(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Transaction).filter_by(user_id=user.id, kind="expense").all(); totals=defaultdict(float); months=defaultdict(float)
    for t in rows: totals[t.category]+=t.amount; months[t.date.strftime("%Y-%m")]+=t.amount
    out=[]
    if totals:
        top=max(totals,key=totals.get); out.append({"type":"category","title":f"{top} is your largest expense","detail":f"You recorded {totals[top]:.2f} in {top} across your history."})
    keys=sorted(months)
    if len(keys)>=2:
        delta=months[keys[-1]]-months[keys[-2]]; pct=abs(delta)/months[keys[-2]]*100 if months[keys[-2]] else 0
        out.append({"type":"trend","title":f"Monthly spending {'increased' if delta>0 else 'decreased'}","detail":f"Your latest recorded month changed by {pct:.0f}% ({abs(delta):.2f}) compared with the prior month."})
    if not rows: out.append({"type":"empty","title":"Add expenses to unlock insights","detail":"Insights are calculated from your transaction history."})
    return out

@app.get("/api/analytics/prediction")
def predict(user: User = Depends(current_user), db: Session = Depends(get_db)): return prediction(db.query(Transaction).filter_by(user_id=user.id).all())

@app.get("/api/budgets")
def list_budgets(user: User = Depends(current_user), db: Session = Depends(get_db)): return [{"id":b.id,"category":b.category,"month":b.month,"amount":b.amount} for b in db.query(Budget).filter_by(user_id=user.id).order_by(Budget.month.desc()).all()]

@app.post("/api/budgets", status_code=201)
def create_budget(data: BudgetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item=Budget(user_id=user.id, **data.model_dump()); db.add(item); db.commit(); db.refresh(item); return {"id":item.id,"category":item.category,"month":item.month,"amount":item.amount}

@app.put("/api/budgets/{bid}")
def update_budget(bid: int, data: BudgetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item=db.query(Budget).filter_by(id=bid,user_id=user.id).first()
    if not item: raise HTTPException(404,"Budget not found")
    for k,v in data.model_dump().items(): setattr(item,k,v)
    db.commit(); return {"id":item.id,"category":item.category,"month":item.month,"amount":item.amount}

@app.delete("/api/budgets/{bid}", status_code=204)
def remove_budget(bid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item=db.query(Budget).filter_by(id=bid,user_id=user.id).first()
    if not item: raise HTTPException(404,"Budget not found")
    db.delete(item); db.commit(); return Response(status_code=204)

@app.get("/api/goals")
def goals(user: User = Depends(current_user), db: Session = Depends(get_db)): return [{"id":g.id,"name":g.name,"target_amount":g.target_amount,"saved_amount":g.saved_amount,"target_date":g.target_date} for g in db.query(SavingsGoal).filter_by(user_id=user.id).all()]

@app.post("/api/goals", status_code=201)
def add_goal(data: GoalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item=SavingsGoal(user_id=user.id,**data.model_dump()); db.add(item); db.commit(); db.refresh(item); return {"id":item.id,"name":item.name,"target_amount":item.target_amount,"saved_amount":item.saved_amount,"target_date":item.target_date}

@app.delete("/api/goals/{gid}", status_code=204)
def delete_goal(gid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item=db.query(SavingsGoal).filter_by(id=gid,user_id=user.id).first()
    if not item: raise HTTPException(404,"Goal not found")
    db.delete(item); db.commit(); return Response(status_code=204)

@app.get("/api/reports/export.csv")
def export_csv(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows=db.query(Transaction).filter_by(user_id=user.id).order_by(Transaction.date.desc()).all(); buffer=io.StringIO(); writer=csv.writer(buffer); writer.writerow(["id","date","type","category","description","amount","payment_method"])
    def safe_cell(value):
        text = str(value)
        if text.lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@"):
            return "'" + text
        return text
    for t in rows: writer.writerow([t.id,t.date,t.kind,safe_cell(t.category),safe_cell(t.description),t.amount,safe_cell(t.payment_method)])
    return Response(buffer.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=fintrack-report.csv"})
