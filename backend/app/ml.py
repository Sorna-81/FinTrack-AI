from collections import defaultdict
from datetime import date
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sqlalchemy.orm import Session
from .models import Transaction

SEEDS = {
    "food": "Food", "restaurant": "Food", "swiggy": "Food", "zomato": "Food", "grocery": "Groceries", "supermarket": "Groceries",
    "uber": "Transportation", "ola": "Transportation", "metro": "Transportation", "fuel": "Transportation", "amazon": "Shopping", "clothes": "Shopping",
    "electricity": "Utilities", "water bill": "Utilities", "internet": "Utilities", "netflix": "Entertainment", "movie": "Entertainment",
    "pharmacy": "Health", "doctor": "Health", "salary": "Salary", "freelance": "Freelance", "rent": "Housing"
}


def categorize(description: str, db: Session, user_id: int) -> dict:
    rows = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.kind == "expense").all()
    texts = [r.description for r in rows if r.description.strip()]
    labels = [r.category for r in rows if r.description.strip()]
    confidence = 0.0
    method = "keyword fallback"
    if len(texts) >= 8 and len(set(labels)) >= 2:
        model = Pipeline([("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=2000)), ("classifier", LogisticRegression(max_iter=500, class_weight="balanced"))])
        model.fit(texts, labels)
        probabilities = model.predict_proba([description])[0]
        idx = int(np.argmax(probabilities))
        category, confidence, method = str(model.classes_[idx]), float(probabilities[idx]), "personalized classifier"
        if confidence >= 0.35:
            return {"category": category, "confidence": round(confidence, 3), "method": method}
    lower = description.lower()
    for word, category in SEEDS.items():
        if word in lower:
            return {"category": category, "confidence": 0.65, "method": method}
    totals = defaultdict(int)
    for label in labels:
        totals[label] += 1
    fallback = max(totals, key=totals.get) if totals else "Other"
    return {"category": fallback, "confidence": 0.15 if labels else 0.0, "method": "manual review recommended"}


def prediction(rows: list[Transaction]) -> dict:
    monthly = defaultdict(float)
    for row in rows:
        if row.kind == "expense":
            monthly[row.date.strftime("%Y-%m")] += row.amount
    keys = sorted(monthly)
    if len(keys) < 3:
        return {"available": False, "reason": "Add at least three months of expenses to build a useful forecast.", "history": [{"month": k, "amount": round(monthly[k], 2)} for k in keys]}
    values = np.array([monthly[k] for k in keys], dtype=float)
    x = np.arange(len(values))
    slope, intercept = np.polyfit(x, values, 1)
    predicted = max(0.0, float(intercept + slope * len(values)))
    return {"available": True, "predicted": round(predicted, 2), "previous": round(float(values[-1]), 2), "trend": "up" if slope > 1 else "down" if slope < -1 else "stable", "history": [{"month": k, "amount": round(monthly[k], 2)} for k in keys], "model": "linear trend; historical monthly totals"}


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _next_month(month: str) -> str:
    year, value = (int(part) for part in month.split("-"))
    return _month_key(year + (value == 12), 1 if value == 12 else value + 1)


def _monthly_series(rows: list[Transaction], kind: str) -> tuple[list[str], np.ndarray]:
    totals = defaultdict(float)
    for row in rows:
        if row.kind == kind:
            totals[row.date.strftime("%Y-%m")] += row.amount
    if not totals:
        return [], np.array([], dtype=float)
    start_year, start_month = (int(part) for part in min(totals).split("-"))
    end_year, end_month = (int(part) for part in max(totals).split("-"))
    keys = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        keys.append(_month_key(year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys, np.array([totals[key] for key in keys], dtype=float)


def _trend(values: np.ndarray, periods: int) -> tuple[np.ndarray, float]:
    x = np.arange(len(values), dtype=float)
    slope, intercept = np.polyfit(x, values, 1)
    forecast_x = np.arange(len(values), len(values) + periods, dtype=float)
    return np.maximum(0, intercept + slope * forecast_x), float(slope)


def expense_forecast(rows: list[Transaction], periods: int = 3) -> dict:
    expense_months, expenses = _monthly_series(rows, "expense")
    history = [{"month": month, "actual_expenses": round(float(amount), 2), "predicted_expenses": None} for month, amount in zip(expense_months, expenses)]
    if len(expense_months) < 3:
        return {
            "available": False,
            "reason": "Add expenses across at least three calendar months to generate a forecast.",
            "history": history,
            "timeline": history,
            "category_predictions": [],
            "recommendations": [],
        }

    projected_expenses, expense_slope = _trend(expenses, periods)
    projection_months = []
    month = expense_months[-1]
    for _ in range(periods):
        month = _next_month(month)
        projection_months.append(month)
    projected_history = [{"month": month, "actual_expenses": None, "predicted_expenses": round(float(amount), 2)} for month, amount in zip(projection_months, projected_expenses)]

    category_totals = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if row.kind == "expense":
            category_totals[row.category][row.date.strftime("%Y-%m")] += row.amount
    recent_months = expense_months[-min(6, len(expense_months)):]
    category_predictions = []
    for category, totals in category_totals.items():
        shares = [totals[month] / expenses[expense_months.index(month)] for month in recent_months if expenses[expense_months.index(month)] > 0]
        if shares:
            category_predictions.append({"category": category, "predicted": round(float(projected_expenses[0] * np.mean(shares)), 2), "historical_share": round(float(np.mean(shares) * 100), 1)})
    category_predictions.sort(key=lambda item: item["predicted"], reverse=True)

    income_months, income = _monthly_series(rows, "income")
    expected_income = expected_savings = None
    if len(income_months) >= 3:
        projected_income, _income_slope = _trend(income, 1)
        expected_income = round(float(projected_income[0]), 2)
        expected_savings = round(expected_income - float(projected_expenses[0]), 2)

    trend = "up" if expense_slope > 1 else "down" if expense_slope < -1 else "stable"
    recommendations = []
    if trend == "up":
        recommendations.append(f"Monthly expenses are trending up by about {abs(expense_slope):.2f} per month; review discretionary spending before next month.")
    elif trend == "down":
        recommendations.append(f"Monthly expenses are trending down by about {abs(expense_slope):.2f} per month; maintain the habits behind the improvement.")
    else:
        recommendations.append("Monthly expenses are broadly stable; use a category budget to preserve that consistency.")
    if category_predictions:
        leading = category_predictions[0]
        recommendations.append(f"{leading['category']} is expected to be your largest expense category next month at about {leading['predicted']:.2f}.")
    if expected_savings is None:
        recommendations.append("Add income across at least three months to estimate next month's savings.")
    elif expected_savings < 0:
        recommendations.append("Projected expenses exceed projected income; consider reducing flexible categories or increasing income before next month.")
    else:
        recommendations.append(f"Projected savings are about {expected_savings:.2f}; consider assigning part of that amount to a savings goal.")

    return {
        "available": True,
        "model": "least-squares linear trend on monthly totals; category forecasts use average observed spending share from the most recent six months",
        "next_month": projection_months[0],
        "next_month_expenses": round(float(projected_expenses[0]), 2),
        "expected_income": expected_income,
        "expected_savings": expected_savings,
        "trend": trend,
        "trend_per_month": round(expense_slope, 2),
        "history": history,
        "timeline": history + projected_history,
        "category_predictions": category_predictions,
        "recommendations": recommendations,
    }
