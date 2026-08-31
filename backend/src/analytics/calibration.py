"""Outcome reports for conservative score calibration.

The report deliberately separates a user's decision (watch/reject/bought) from
the later recovery outcome. A bought case without an explicit outcome remains
``unresolved`` and is never counted as a failed recovery.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

OUTCOMES = frozenset({"in_progress", "recovered", "not_recovered"})


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return Decimal(0)


def feedback_outcome(row: Any) -> str:
    """Return a conservative normalized outcome for one feedback row."""
    explicit = str(getattr(row, "outcome", "") or "").strip().lower()
    if explicit in OUTCOMES:
        return explicit
    recovered_amount = getattr(row, "recovered_amount", None)
    # Backward-compatible inference for records created before ``outcome`` was
    # added. A missing amount remains unresolved; zero is an explicit loss.
    if str(getattr(row, "action", "")) == "bought" and recovered_amount is not None:
        return "recovered" if _decimal(recovered_amount) > 0 else "not_recovered"
    return "unresolved"


def _new_bucket(score_class: str | None) -> dict[str, Any]:
    return {
        "score_class": score_class,
        "bought": 0,
        "resolved": 0,
        "recovered": 0,
        "unresolved": 0,
        "recovery_rate": None,
        "recovered_amount": Decimal(0),
        "expense_amount": Decimal(0),
        "net_recovered_amount": Decimal(0),
        "avg_predicted_ev": None,
        "mean_abs_recovered_vs_ev": None,
    }


def build_calibration_report(rows: list[Any], *, min_resolved: int = 10) -> dict[str, Any]:
    """Build a JSON-serializable calibration report from ORM-like rows.

    ``decision_*`` fields are copied to :class:`UserFeedback` at submission
    time, so later score changes cannot leak future information into the
    historical report.
    """
    action_counts = Counter(str(getattr(row, "action", "")) for row in rows)
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: _new_bucket(None))
    all_bucket = _new_bucket(None)
    versions: set[str] = set()
    total_recovered = Decimal(0)
    total_expenses = Decimal(0)
    absolute_errors: list[Decimal] = []
    resolved_count = 0
    recovered_count = 0
    unresolved_count = 0

    for row in rows:
        if str(getattr(row, "action", "")) != "bought":
            continue
        class_value = getattr(row, "decision_score_class", None) or "unknown"
        bucket = buckets[class_value]
        outcome = feedback_outcome(row)
        bucket["bought"] += 1
        all_bucket["bought"] += 1
        if outcome == "unresolved" or outcome == "in_progress":
            bucket["unresolved"] += 1
            all_bucket["unresolved"] += 1
            unresolved_count += 1
        else:
            bucket["resolved"] += 1
            all_bucket["resolved"] += 1
            resolved_count += 1
            if outcome == "recovered":
                bucket["recovered"] += 1
                all_bucket["recovered"] += 1
                recovered_count += 1

            recovered_amount = _decimal(getattr(row, "recovered_amount", None))
            expense_amount = _decimal(getattr(row, "expense_amount", None))
            bucket["recovered_amount"] += recovered_amount
            bucket["expense_amount"] += expense_amount
            bucket["net_recovered_amount"] += recovered_amount - expense_amount
            total_recovered += recovered_amount
            total_expenses += expense_amount
            predicted_ev = getattr(row, "decision_score_ev", None)
            if predicted_ev is not None:
                absolute_errors.append(abs(recovered_amount - _decimal(predicted_ev)))

        version = str(getattr(row, "decision_score_version", "") or "").strip()
        if version:
            versions.add(version)

    def finalize(bucket: dict[str, Any]) -> dict[str, Any]:
        resolved = int(bucket["resolved"])
        if resolved:
            bucket["recovery_rate"] = Decimal(bucket["recovered"]) / Decimal(resolved)
        predicted_values = [
            _decimal(getattr(row, "decision_score_ev", None))
            for row in rows
            if str(getattr(row, "action", "")) == "bought"
            and (getattr(row, "decision_score_class", None) or "unknown")
            == bucket["score_class"]
            and feedback_outcome(row) in {"recovered", "not_recovered"}
            and getattr(row, "decision_score_ev", None) is not None
        ]
        if predicted_values:
            bucket["avg_predicted_ev"] = sum(predicted_values, Decimal(0)) / Decimal(
                len(predicted_values)
            )
        errors = [
            abs(
                _decimal(getattr(row, "recovered_amount", None))
                - _decimal(getattr(row, "decision_score_ev", None))
            )
            for row in rows
            if str(getattr(row, "action", "")) == "bought"
            and (getattr(row, "decision_score_class", None) or "unknown")
            == bucket["score_class"]
            and feedback_outcome(row) in {"recovered", "not_recovered"}
            and getattr(row, "decision_score_ev", None) is not None
        ]
        if errors:
            bucket["mean_abs_recovered_vs_ev"] = sum(errors, Decimal(0)) / Decimal(len(errors))
        return bucket

    by_class = [
        finalize(buckets[key])
        for key in sorted(buckets, key=lambda value: (value == "unknown", value))
    ]
    all_bucket["score_class"] = None
    all_bucket = finalize(all_bucket)
    return {
        "status": "ready" if resolved_count >= max(1, min_resolved) else "insufficient_data",
        "min_resolved": max(1, min_resolved),
        "total_feedback": len(rows),
        "decision_counts": {
            "watch": int(action_counts.get("watch", 0)),
            "reject": int(action_counts.get("reject", 0)),
            "bought": int(action_counts.get("bought", 0)),
        },
        "purchases": int(action_counts.get("bought", 0)),
        "resolved_purchases": resolved_count,
        "unresolved_purchases": unresolved_count,
        "recovered_purchases": recovered_count,
        "recovery_rate": (
            Decimal(recovered_count) / Decimal(resolved_count) if resolved_count else None
        ),
        "recovered_amount": total_recovered,
        "expense_amount": total_expenses,
        "net_recovered_amount": total_recovered - total_expenses,
        "mean_abs_recovered_vs_ev": (
            sum(absolute_errors, Decimal(0)) / Decimal(len(absolute_errors))
            if absolute_errors
            else None
        ),
        "model_versions": sorted(versions),
        "by_class": by_class,
    }
