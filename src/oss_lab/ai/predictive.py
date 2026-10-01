"""Workflow failure prediction: estimates which orders are likely to fail before they
execute. Advisory only; evaluated against labeled history with tracked precision/recall."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

FEATURES = (
    "is_loopback",
    "device_recent_failures",
    "mtu_extreme",
    "interface_busy",
    "concurrent_orders",
)


def synthetic_history(n: int = 2000, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic labeled order history. Failures are driven by flaky devices, unusual
    payloads, contended interfaces, and load."""
    rng = np.random.default_rng(seed)
    X = np.column_stack([
        rng.integers(0, 2, n), rng.poisson(1.0, n), rng.integers(0, 2, n),
        rng.integers(0, 2, n), rng.poisson(3.0, n),
    ]).astype(float)  # fmt: skip
    logit = -4.0 + 0.9 * X[:, 1] + 1.8 * X[:, 2] + 2.5 * X[:, 3] + 0.25 * X[:, 4]
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return X, y


@dataclass
class FailurePredictor:
    threshold: float = 0.5

    def fit(self, X: np.ndarray, y: np.ndarray) -> FailurePredictor:
        self.model_ = LogisticRegression(max_iter=1000, class_weight="balanced").fit(X, y)
        return self

    def risk(self, X: np.ndarray) -> np.ndarray:
        return self.model_.predict_proba(np.atleast_2d(X))[:, 1]

    def flag(self, X: np.ndarray) -> np.ndarray:
        return self.risk(X) >= self.threshold

    def explain(self) -> dict[str, float]:
        return dict(zip(FEATURES, (float(c) for c in self.model_.coef_[0]), strict=True))


def evaluate(seed: int = 0) -> dict[str, float]:
    X, y = synthetic_history(seed=seed)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=seed, stratify=y)
    pred = FailurePredictor().fit(Xtr, ytr).flag(Xte)
    tp = int(((pred == 1) & (yte == 1)).sum())
    return {
        "precision": tp / max(int(pred.sum()), 1),
        "recall": tp / max(int(yte.sum()), 1),
        "failure_rate": float(y.mean()),
    }
