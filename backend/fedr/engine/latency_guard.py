"""LATENCY GUARD - timestamps for every stage; stale opportunities are rejected."""

from __future__ import annotations

from dataclasses import dataclass, field

from fedr.core.models import ExecutionQuote, now_ms


@dataclass(slots=True)
class Timeline:
    market_data_ts_ms: int = 0
    quote_ts_ms: int = 0
    decision_ts_ms: int = 0
    submission_ts_ms: int = 0
    fill_ts_ms: int = 0
    notes: list[str] = field(default_factory=list)

    def mark_decision(self) -> None:
        self.decision_ts_ms = now_ms()

    def mark_submission(self) -> None:
        self.submission_ts_ms = now_ms()

    def mark_fill(self) -> None:
        self.fill_ts_ms = now_ms()

    def as_dict(self) -> dict[str, int]:
        return {
            "market_data_ts_ms": self.market_data_ts_ms,
            "quote_ts_ms": self.quote_ts_ms,
            "decision_ts_ms": self.decision_ts_ms,
            "submission_ts_ms": self.submission_ts_ms,
            "fill_ts_ms": self.fill_ts_ms,
            "data_to_decision_ms": (self.decision_ts_ms - self.market_data_ts_ms)
            if self.decision_ts_ms
            else 0,
            "decision_to_submit_ms": (self.submission_ts_ms - self.decision_ts_ms)
            if self.submission_ts_ms
            else 0,
            "submit_to_fill_ms": (self.fill_ts_ms - self.submission_ts_ms) if self.fill_ts_ms else 0,
        }


class LatencyGuard:
    def __init__(self, max_quote_age_ms: int):
        self.max_quote_age_ms = max_quote_age_ms

    def timeline_for(self, *legs: ExecutionQuote) -> Timeline:
        return Timeline(
            market_data_ts_ms=min(l.market_ts_ms for l in legs),
            quote_ts_ms=min(l.quote_ts_ms for l in legs),
        )

    def check(self, *legs: ExecutionQuote, at_ms: int | None = None) -> list[str]:
        at = at_ms or now_ms()
        reasons = []
        for leg in legs:
            age = at - min(leg.market_ts_ms, leg.quote_ts_ms)
            if age > self.max_quote_age_ms:
                reasons.append(f"{leg.venue} quote is {age} ms old (max {self.max_quote_age_ms} ms)")
        return reasons
