"""Learning-to-rank from interaction logs.

Each logged impression/click/save/apply carries the five score components at the time it was shown.
We fit a logistic regression (numpy, L2) predicting engagement (click/save/apply = 1, impression or
dismiss = 0, with save/apply weighted higher), then map the positive coefficients to new component
weights. Weights are only written when there is enough data and they improve held-out AUC.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .config import get_settings
from .scoring import COMPONENTS, DEFAULT_WEIGHTS

LABEL = {"impression": 0.0, "dismiss": 0.0, "click": 1.0, "save": 1.0, "apply": 1.0}
SAMPLE_WEIGHT = {"impression": 1.0, "dismiss": 2.0, "click": 1.0, "save": 2.0, "apply": 3.0}
MIN_EVENTS = 200


def fit_logreg(X: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = 1e-2, iters: int = 500, lr: float = 0.5):
    Xb = np.hstack([X, np.ones((len(X), 1))])
    theta = np.zeros(Xb.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Xb @ theta))
        grad = Xb.T @ (w * (p - y)) / w.sum() + l2 * np.r_[theta[:-1], 0]
        theta -= lr * grad
    return theta


def auc(scores: np.ndarray, y: np.ndarray) -> float:
    pos, neg = scores[y == 1], scores[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def train_from_rows(rows: list[dict], seed: int = 0) -> dict:
    rows = [r for r in rows if r.get("features") and all(k in r["features"] for k in COMPONENTS)]
    if len(rows) < MIN_EVENTS:
        return {"trained": False, "reason": f"need {MIN_EVENTS} events with features, have {len(rows)}"}
    X = np.array([[float(r["features"][k]) for k in COMPONENTS] for r in rows])
    y = np.array([LABEL[r["event"]] for r in rows])
    sw = np.array([SAMPLE_WEIGHT[r["event"]] for r in rows])
    if y.min() == y.max():
        return {"trained": False, "reason": "only one class present"}
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(X))
    cut = int(0.8 * len(X))
    tr, te = idx[:cut], idx[cut:]
    theta = fit_logreg(X[tr], y[tr], sw[tr])
    base = np.array([DEFAULT_WEIGHTS[k] for k in COMPONENTS])
    auc_new = auc(X[te] @ theta[:-1], y[te])
    auc_base = auc(X[te] @ base, y[te])
    coef = np.clip(theta[:-1], 0, None)
    if coef.sum() == 0:
        return {"trained": False, "reason": "no positive coefficients", "auc_base": auc_base}
    # shrink towards the defaults so a small log can't swing the ranking wildly
    lam = min(1.0, len(rows) / 5000)
    weights = (1 - lam) * base + lam * coef / coef.sum()
    weights = weights / weights.sum()
    return {"trained": True, "weights": dict(zip(COMPONENTS, map(float, weights))), "auc_new": auc_new,
            "auc_base": auc_base, "n": len(rows), "improved": bool(auc_new > auc_base)}


def train_and_save() -> dict:
    from .db import conn

    with conn() as c:
        rows = c.execute("SELECT event, features FROM job_interactions WHERE features IS NOT NULL "
                         "ORDER BY id DESC LIMIT 100000").fetchall()
    res = train_from_rows(rows)
    if res.get("trained") and res.get("improved"):
        Path(get_settings().ltr_weights_path).write_text(json.dumps(
            {"weights": res["weights"], "trained_at": datetime.now(timezone.utc).isoformat(),
             "n": res["n"], "auc": res["auc_new"], "auc_base": res["auc_base"]}, indent=2))
        res["saved"] = True
    return res
