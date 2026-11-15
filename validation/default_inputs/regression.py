"""Capacity regressions with model/family held-out validation and nested selection.

The response is original geometric cm3/rev for compressors and m3/s for fans.
Capacity x is kW. No displacement or capacity is rescaled to another speed.
"""

from __future__ import annotations

import numpy as np

KINDS = ("proportional", "linear", "power")


def predict(law: dict, x: np.ndarray | float) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if law["kind"] == "proportional":
        return law["a"] * x
    if law["kind"] == "linear":
        return law["a"] * x + law["b"]
    if law["kind"] == "power":
        return law["a"] * x ** law["b"]
    raise ValueError(f"Unknown regression kind: {law['kind']}")


def fit_law(rows: list[dict], kind: str) -> dict:
    if not rows:
        raise ValueError("Empty training set")
    x = np.array([r["capacity_W"] / 1000 for r in rows])
    y = np.array([r["value"] for r in rows])
    if not np.all(np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)):
        raise ValueError("Training capacities and values must be positive and finite")
    groups = [r["family_id"] for r in rows]
    weights = np.array([1 / groups.count(g) for g in groups])
    sw = np.sqrt(weights)
    if kind == "proportional":
        a = float(np.sum(weights * x * y) / np.sum(weights * x * x))
        law = dict(kind=kind, a=a)
    else:
        if len(np.unique(x)) < 2:
            raise ValueError("Two distinct training capacities required")
        if kind == "linear":
            a, b = np.linalg.lstsq(np.column_stack([x, np.ones(len(x))]) * sw[:, None], y * sw, rcond=None)[0]
            law = dict(kind=kind, a=float(a), b=float(b))
        elif kind == "power":
            b, loga = np.linalg.lstsq(
                np.column_stack([np.log(x), np.ones(len(x))]) * sw[:, None], np.log(y) * sw, rcond=None
            )[0]
            law = dict(kind=kind, a=float(np.exp(loga)), b=float(b))
        else:
            raise ValueError(f"Unknown regression kind: {kind}")
    slope = law["a"] if kind != "power" else law["b"]
    if slope <= 0 or not np.all(np.isfinite(predict(law, x)) & (predict(law, x) > 0)):
        raise ValueError("Nonpositive or nonmonotone fitted law")
    return law


def metrics(rows: list[dict]) -> dict:
    if not rows or any(not np.isfinite(r["predicted"]) or r["predicted"] <= 0 for r in rows):
        return {"n": len(rows), "valid": False}
    y = np.array([r["observed"] for r in rows])
    p = np.array([r["predicted"] for r in rows])
    error = p - y
    return dict(
        n=len(y),
        valid=True,
        MAE=float(np.mean(abs(error))),
        RMSE=float(np.sqrt(np.mean(error**2))),
        MAPE_pct=float(np.mean(abs(error) / y) * 100),
        bias_pct=float(np.mean(error / y) * 100),
    )


def cv_fixed(rows: list[dict], kind: str, group_field: str = "family_id") -> list[dict]:
    out = []
    for group in sorted({r[group_field] for r in rows}):
        train = [r for r in rows if r[group_field] != group]
        test = [r for r in rows if r[group_field] == group]
        try:
            law = fit_law(train, kind)
            values = predict(law, [r["capacity_W"] / 1000 for r in test])
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            values = np.full(len(test), np.nan)
        for r, value in zip(test, values, strict=True):
            out.append(
                dict(
                    record_id=r["record_id"],
                    family_id=r["family_id"],
                    model_id=r["model_id"],
                    fold=group,
                    observed=r["value"],
                    predicted=float(value),
                    capacity_W=r["capacity_W"],
                    train_groups=sorted({t[group_field] for t in train}),
                )
            )
    return out


def select_kind(rows: list[dict]) -> str:
    """Prefer the simplest candidate within 2% of best family-CV RMSE."""
    candidates = []
    for kind in KINDS:
        score = metrics(cv_fixed(rows, kind))
        if score.get("valid"):
            candidates.append((kind, score["RMSE"]))
    if not candidates:
        # Only an origin-constrained law is identifiable with a single
        # capacity/family in an inner training split. This is flagged later.
        fit_law(rows, "proportional")
        return "proportional"
    best = min(s for _, s in candidates)
    return next(kind for kind, score in candidates if score <= best * 1.02 + 1e-15)


def nested_cv(rows: list[dict], group_field: str = "family_id") -> list[dict]:
    out = []
    for group in sorted({r[group_field] for r in rows}):
        train = [r for r in rows if r[group_field] != group]
        test = [r for r in rows if r[group_field] == group]
        if not train:
            continue
        kind = select_kind(train)
        law = fit_law(train, kind)
        pred = predict(law, [r["capacity_W"] / 1000 for r in test])
        for r, value in zip(test, pred, strict=True):
            out.append(
                dict(
                    record_id=r["record_id"],
                    family_id=r["family_id"],
                    model_id=r["model_id"],
                    fold=group,
                    selected_in_training=kind,
                    observed=r["value"],
                    predicted=float(value),
                    capacity_W=r["capacity_W"],
                    train_groups=sorted({t[group_field] for t in train}),
                    test_is_extrapolation=not min(t["capacity_W"] for t in train)
                    <= r["capacity_W"]
                    <= max(t["capacity_W"] for t in train),
                )
            )
    return out
