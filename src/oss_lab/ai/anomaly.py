"""KPI anomaly detection: seasonal robust baseline for single KPIs, Isolation Forest
for multi-KPI correlation. Output is advisory."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import IsolationForest

KPIS = ("workflow_latency_ms", "error_rate", "sync_drift", "queue_lag")


def synthetic_kpis(n: int = 24 * 14, period: int = 24, seed: int = 0, n_anomalies: int = 8):
    """Seasonal KPI matrix with injected multi-KPI incidents. Returns (X, truth_indices)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    season = np.sin(2 * np.pi * t / period)
    X = np.column_stack(
        [
            200 + 40 * season + rng.normal(0, 5, n),
            0.01 + 0.004 * season + rng.normal(0, 0.001, n),
            2 + 0.5 * season + rng.normal(0, 0.2, n),
            50 + 10 * season + rng.normal(0, 2, n),
        ]
    )
    idx = rng.choice(np.arange(period, n), size=n_anomalies, replace=False)
    X[idx, 0] *= 3
    X[idx, 1] *= 8
    X[idx, 3] *= 4
    return X, sorted(int(i) for i in idx)


@dataclass
class SeasonalBaseline:
    period: int = 24
    threshold: float = 6.0  # robust z-score

    def fit(self, series: np.ndarray) -> SeasonalBaseline:
        series = np.asarray(series, dtype=float)
        self.base_ = np.array([np.median(series[p :: self.period]) for p in range(self.period)])
        resid = series - self.base_[np.arange(len(series)) % self.period]
        self.scale_ = max(1.4826 * np.median(np.abs(resid - np.median(resid))), 1e-9)
        return self

    def zscores(self, series: np.ndarray) -> np.ndarray:
        series = np.asarray(series, dtype=float)
        resid = series - self.base_[np.arange(len(series)) % self.period]
        return resid / self.scale_

    def detect(self, series: np.ndarray) -> list[int]:
        return [int(i) for i in np.flatnonzero(np.abs(self.zscores(series)) > self.threshold)]


class MultiKpiDetector:
    """Detrends each KPI with its seasonal baseline, then isolates joint outliers."""

    def __init__(self, period: int = 24, contamination: float = 0.02, seed: int = 0) -> None:
        self.period = period
        self.forest = IsolationForest(
            contamination=contamination, random_state=seed, n_estimators=200
        )

    def fit(self, X: np.ndarray) -> MultiKpiDetector:
        self.baselines_ = [SeasonalBaseline(self.period).fit(X[:, j]) for j in range(X.shape[1])]
        self.forest.fit(self._features(X))
        return self

    def _features(self, X: np.ndarray) -> np.ndarray:
        return np.column_stack([b.zscores(X[:, j]) for j, b in enumerate(self.baselines_)])

    def detect(self, X: np.ndarray) -> list[int]:
        return [int(i) for i in np.flatnonzero(self.forest.predict(self._features(X)) == -1)]


def precision_recall(truth: list[int], predicted: list[int]) -> tuple[float, float]:
    t, p = set(truth), set(predicted)
    tp = len(t & p)
    return (tp / len(p) if p else 1.0, tp / len(t) if t else 1.0)
