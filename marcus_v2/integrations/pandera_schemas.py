"""Pandera schemas for market observations (deterministic, no LLM involvement)."""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

PRICE_TYPES = ["official_close", "provisional_intraday", "delayed", "indicative", "historical", "unsupported"]

observation_schema = DataFrameSchema(
    {
        "observation_id": Column(str, nullable=False, unique=True),
        "event_id": Column(str, nullable=True),
        "instrument_id": Column(str, nullable=False),
        "symbol": Column(str, nullable=False),
        "currency": Column(str, Check.str_matches(r"^[A-Z]{3}$"), nullable=False),
        "price_unit": Column(str, nullable=False),
        "source_provider": Column(str, nullable=False),
        "retrieved_at": Column("datetime64[ns, UTC]", nullable=False),
        "observed_at": Column("datetime64[ns, UTC]", nullable=True),
        "price_type": Column(str, Check.isin(PRICE_TYPES), nullable=False),
        "open": Column(float, Check.gt(0), nullable=True),
        "high": Column(float, Check.gt(0), nullable=True),
        "low": Column(float, Check.gt(0), nullable=True),
        "close": Column(float, [Check.gt(0), Check(lambda s: s.apply(lambda v: v == v and v not in (float("inf"),)), element_wise=False)], nullable=True),
        "volume": Column(float, Check.ge(0), nullable=True),
    },
    checks=[
        Check(lambda df: (df["high"].isna() | df["low"].isna()) | (df["high"] >= df["low"]), name="high_ge_low"),
        Check(lambda df: df["open"].isna() | df["high"].isna() | df["low"].isna()
              | ((df["open"] <= df["high"] * 1.0000001) & (df["open"] >= df["low"] * 0.9999999)), name="open_within_range"),
        Check(lambda df: df["close"].isna() | df["high"].isna() | df["low"].isna()
              | ((df["close"] <= df["high"] * 1.0000001) & (df["close"] >= df["low"] * 0.9999999)), name="close_within_range"),
    ],
    coerce=True,
    strict=False,
)


def observations_frame(observations) -> pd.DataFrame:
    rows = []
    for o in observations:
        d = o.model_dump(mode="python")
        rows.append({
            "observation_id": d["observation_id"], "event_id": d["event_id"], "instrument_id": d["instrument_id"],
            "symbol": d["symbol"], "currency": d["currency"], "price_unit": d["price_unit"],
            "source_provider": d["source_provider"], "retrieved_at": d["retrieved_at"], "observed_at": d["observed_at"],
            "price_type": o.price_type.value,
            **{k: (None if d[k] is None else float(d[k])) for k in ("open", "high", "low", "close", "volume")},
        })
    df = pd.DataFrame(rows)
    for c in ("retrieved_at", "observed_at"):
        if c in df:
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def run_schema(df: pd.DataFrame) -> list[dict]:
    """Return failure cases as dicts (empty list = schema passed)."""
    if df.empty:
        return [{"check": "non_empty", "column": None, "index": None, "failure_case": "no observations"}]
    try:
        observation_schema.validate(df, lazy=True)
        return []
    except pa.errors.SchemaErrors as err:
        fc = err.failure_cases
        out = []
        for _, r in fc.iterrows():
            out.append({"check": str(r.get("check")), "column": r.get("column"),
                        "index": None if pd.isna(r.get("index")) else int(r.get("index")),
                        "failure_case": None if pd.isna(r.get("failure_case")) else str(r.get("failure_case"))})
        return out
