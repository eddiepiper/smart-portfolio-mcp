"""Data Guardian agent (foundation: Pandera).

Deterministic validation decides VALID / WARNING / REJECTED. The optional LLM
step only explains exceptions; it cannot change the status, repair a price or
touch the source record (observations are validated on a copy).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from ...core.contracts import (DataQualityReport, MarketIntelligenceReport, MarketObservation, PriceType,
                               QualityStatus, Severity, ValidationIssue, new_id, utcnow)
from ...core.sessions import classify_daily_bar, last_completed_session
from ...integrations.pandera_schemas import observations_frame, run_schema
from ..base import Agent, load_instruments

TROY_OZ_GRAMS = Decimal("31.1034768")

PURPOSES = {"research", "decision", "execution"}


class DataGuardian(Agent):
    agent_id = "data_guardian"

    def __init__(self, rt, clock=utcnow):
        super().__init__(rt)
        self.clock = clock
        reg = load_instruments()
        self.registry = {i["instrument_id"]: i for i in reg["instruments"]}
        self.thresholds = reg["thresholds"]

    def register(self):
        for name in ("validate_report", "validate_observations", "compare_provider_observations",
                     "generate_data_quality_report", "explain_exceptions"):
            self.tool(name, getattr(self, name), "validate_data")
        return self

    def _th(self, instrument_id: str) -> dict:
        return {**self.thresholds["default"], **self.thresholds.get(instrument_id, {})}

    # ----------------------------------------------------------------- core run
    def validate_observations(self, observations: list[MarketObservation], *, correlation_id: str,
                              purpose: str = "decision", reference_price: Optional[Decimal] = None,
                              seen_event_ids: Optional[set[str]] = None) -> DataQualityReport:
        if purpose not in PURPOSES:
            raise ValueError(f"purpose must be one of {PURPOSES}")
        obs = [o.model_copy(deep=True) for o in observations]          # never touch the source record
        issues: list[ValidationIssue] = []
        bad: set[str] = set()

        def flag(check, sev, msg, o=None, field=None, **evidence):
            issues.append(ValidationIssue(check=check, severity=sev, message=msg, field=field,
                                          observation_id=o.observation_id if o else None, evidence=evidence))
            if o is not None and sev in (Severity.ERROR, Severity.CRITICAL):
                bad.add(o.observation_id)

        if not obs:
            flag("non_empty", Severity.CRITICAL, "no observations supplied; nothing may be inferred")
            return self._finish(correlation_id, obs, issues, bad)

        # 1. Pandera schema (types, required fields, positivity, OHLC consistency)
        df = observations_frame(obs)
        for f in run_schema(df):
            o = obs[f["index"]] if f["index"] is not None and f["index"] < len(obs) else None
            flag(f"schema:{f['check']}", Severity.ERROR,
                 f"schema check {f['check']} failed on {f['column']}: {f['failure_case']}", o, f["column"])

        now = self.clock()
        # 2. business rules
        seen: dict[str, MarketObservation] = {}
        prior_ids = seen_event_ids or set()
        for o in obs:
            reg = self.registry.get(o.instrument_id)
            if reg is None:
                flag("instrument_known", Severity.ERROR, f"{o.instrument_id} not in instrument registry", o)
                continue
            if o.symbol != reg["symbol"] or o.market != reg["market"]:
                flag("instrument_identity", Severity.ERROR,
                     f"identifier mismatch: got {o.symbol}/{o.market}, registry {reg['symbol']}/{reg['market']}", o)
            if o.currency != reg["currency"]:
                flag("currency", Severity.ERROR, f"currency {o.currency} != instrument currency {reg['currency']}", o, "currency")
            if o.price_unit != reg["price_unit"]:
                flag("price_unit", Severity.ERROR, f"unit {o.price_unit} != instrument unit {reg['price_unit']}", o, "price_unit")
            # duplicates (within batch and against already-processed event ids)
            if o.event_id:
                if o.event_id in prior_ids:
                    flag("duplicate_event", Severity.WARNING, f"event {o.event_id} already processed; ignored", o)
                    bad.add(o.observation_id)
                elif o.event_id in seen:
                    first = seen[o.event_id]
                    same = (first.close, first.bar_date) == (o.close, o.bar_date)
                    flag("duplicate_event", Severity.WARNING if same else Severity.ERROR,
                         f"duplicate event {o.event_id}" + ("" if same else f" with conflicting values {first.close} vs {o.close}"), o)
                    bad.add(o.observation_id)
                else:
                    seen[o.event_id] = o
            else:
                flag("event_id", Severity.WARNING, "observation has no upstream event id; de-duplication limited", o)
            # timestamps
            if o.bar_date is None:
                flag("timestamp", Severity.ERROR, "missing bar date", o, "bar_date")
                continue
            if o.observed_at is None:
                flag("source_timestamp", Severity.WARNING, "provider supplied no source timestamp", o, "observed_at")
            elif o.observed_at > now + timedelta(minutes=5):
                flag("source_timestamp", Severity.ERROR, f"source timestamp {o.observed_at} is in the future", o, "observed_at")
            if o.retrieved_at > now + timedelta(minutes=5):
                flag("retrieval_timestamp", Severity.ERROR, "retrieval timestamp in the future", o, "retrieved_at")
            # price classification: re-derived, never trusted from the producer
            derived = classify_daily_bar(o.bar_date, o.asset_class, now)
            if o.price_type == PriceType.OFFICIAL_CLOSE and derived != PriceType.OFFICIAL_CLOSE:
                flag("price_type", Severity.ERROR,
                     f"labelled official_close but bar {o.bar_date} classifies as {derived.value}", o, "price_type",
                     derived=derived.value)
            if derived == PriceType.UNSUPPORTED:
                flag("price_type", Severity.ERROR, f"bar {o.bar_date} cannot be classified (future or outside calendar)", o)
            if derived == PriceType.PROVISIONAL_INTRADAY and purpose in ("decision", "execution"):
                flag("provisional", Severity.WARNING,
                     f"bar {o.bar_date} is provisional intraday data; not usable as a daily close", o, derived=derived.value)
                bad.add(o.observation_id)
            o.price_type = derived

        # 3. unit plausibility vs a validated reference price (e.g. per-gram vs per-ounce gold)
        if reference_price:
            for o in obs:
                if o.close is None:
                    continue
                ratio = o.close / reference_price
                for factor, label in ((TROY_OZ_GRAMS, "per-ounce vs per-gram"), (1 / TROY_OZ_GRAMS, "per-gram vs per-ounce"),
                                      (Decimal(100), "cents vs dollars"), (Decimal("0.01"), "dollars vs cents")):
                    if abs(ratio / factor - 1) < Decimal("0.15"):
                        flag("quote_convention", Severity.ERROR,
                             f"price {o.close} is ~{factor:.4g}x the reference {reference_price}: likely {label} unit error", o,
                             ratio=str(ratio))

        # 4. jumps between consecutive bars
        by_inst: dict[str, list[MarketObservation]] = {}
        for o in obs:
            if o.close is not None and o.bar_date is not None and o.observation_id not in bad:
                by_inst.setdefault(o.instrument_id, []).append(o)
        for iid, lst in by_inst.items():
            lst.sort(key=lambda x: x.bar_date)
            limit = Decimal(str(self._th(iid)["max_daily_move_pct"]))
            for a, b in zip(lst, lst[1:]):
                mv = abs(b.close / a.close - 1) * 100
                if mv > limit:
                    flag("price_jump", Severity.WARNING,
                         f"{iid} moved {mv:.1f}% {a.bar_date}->{b.bar_date} (> {limit}%): verify before use", b)

        # 5. freshness
        lcs = last_completed_session(now)
        for iid, lst in by_inst.items():
            latest = max(o.bar_date for o in lst)
            max_age = timedelta(hours=self._th(iid)["max_age_hours_official"])
            ref = datetime.combine(latest, datetime.min.time(), tzinfo=now.tzinfo)
            stale = (now - ref) > max_age or (lcs is not None and lst[0].asset_class.value in ("equity", "etf") and latest < lcs)
            if stale:
                sev = Severity.ERROR if purpose in ("decision", "execution") else Severity.WARNING
                flag("freshness", sev, f"{iid} latest bar {latest} is stale (last completed session {lcs})")
                if sev == Severity.ERROR:
                    bad.update(o.observation_id for o in lst)
        return self._finish(correlation_id, obs, issues, bad)

    def _finish(self, correlation_id, obs, issues, bad) -> DataQualityReport:
        if any(i.severity in (Severity.ERROR, Severity.CRITICAL) for i in issues):
            status = QualityStatus.REJECTED
        elif issues:
            status = QualityStatus.WARNING
        else:
            status = QualityStatus.VALID
        rep = DataQualityReport(correlation_id=correlation_id, status=status, checked_observations=len(obs),
                                issues=issues,
                                accepted_observation_ids=[o.observation_id for o in obs if o.observation_id not in bad],
                                rejected_observation_ids=sorted(bad))
        self.rt.evidence.put(rep, "data_quality_report")
        return rep

    # -------------------------------------------------------------------- tools
    def validate_report(self, report: MarketIntelligenceReport, *, purpose: str = "decision",
                        reference_price: Optional[Decimal] = None) -> DataQualityReport:
        dq = self.validate_observations(report.observations, correlation_id=report.correlation_id,
                                        purpose=purpose, reference_price=reference_price)
        report.data_quality_report_id = dq.report_id
        report.data_quality_status = dq.status
        for o in report.observations:
            o.validation_status = QualityStatus.REJECTED if o.observation_id in dq.rejected_observation_ids else (
                QualityStatus.WARNING if any(i.observation_id == o.observation_id for i in dq.issues) else QualityStatus.VALID)
        return dq

    def compare_provider_observations(self, a: Iterable[MarketObservation], b: Iterable[MarketObservation], *,
                                      correlation_id: str) -> DataQualityReport:
        a, b = list(a), list(b)
        issues = []
        bm = {(o.instrument_id, o.bar_date): o for o in b}
        for o in a:
            other = bm.get((o.instrument_id, o.bar_date))
            if other is None or o.close is None or other.close is None:
                continue
            th = Decimal(str(self._th(o.instrument_id)["max_provider_diff_pct"]))
            diff = abs(o.close - other.close) / other.close * 100
            if diff > th * 4:
                issues.append(ValidationIssue(check="provider_discrepancy", severity=Severity.ERROR, observation_id=o.observation_id,
                                              message=f"{o.instrument_id} {o.bar_date}: {o.source_provider} {o.close} vs {other.source_provider} {other.close} ({diff:.3f}%), unexplained",
                                              evidence={"diff_pct": str(diff)}))
            elif diff > th:
                issues.append(ValidationIssue(check="provider_discrepancy", severity=Severity.WARNING, observation_id=o.observation_id,
                                              message=f"{o.instrument_id} {o.bar_date}: {o.source_provider} {o.close} vs {other.source_provider} {other.close} ({diff:.3f}%)",
                                              evidence={"diff_pct": str(diff)}))
        bad = {i.observation_id for i in issues if i.severity == Severity.ERROR}
        return self._finish(correlation_id, a, issues, bad)

    def generate_data_quality_report(self, report: MarketIntelligenceReport, **kw) -> DataQualityReport:
        return self.validate_report(report, **kw)

    def explain_exceptions(self, dq: DataQualityReport) -> DataQualityReport:
        """LLM narrative for exceptions only. Status is copied, never recomputed."""
        if not dq.issues:
            return dq
        status_before = dq.status
        prompt = "Explain these market-data validation findings for an investment lead, briefly:\n" + "\n".join(
            f"- [{i.severity.value}] {i.check}: {i.message}" for i in dq.issues[:30])
        resp = self.rt.llm.complete(self.instructions or "You explain data-quality findings.", prompt)
        dq.explanation = resp.text if resp.text else "(no LLM available: findings above are the explanation)"
        assert dq.status == status_before
        return dq
