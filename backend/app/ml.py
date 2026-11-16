from collections import defaultdict
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
