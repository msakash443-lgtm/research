"""Deterministic pooling of independently sampled correlations on Fisher's z scale."""

import math
from statistics import NormalDist


class MetaAnalysisError(ValueError):
    pass


def pool(studies: list[dict]) -> dict:
    if len(studies) < 2 or len({s["source_id"] for s in studies}) != len(studies):
        raise MetaAnalysisError("At least two distinct studies are required")
    rows = []
    for study in studies:
        r, n = study["r"], study["n"]
        if not isinstance(r, (int, float)) or isinstance(r, bool) or not math.isfinite(r) or abs(r) >= 1:
            raise MetaAnalysisError("Correlation must be finite and strictly between -1 and 1")
        if not isinstance(n, int) or isinstance(n, bool) or n <= 3:
            raise MetaAnalysisError("Sample size must be an integer greater than 3")
        z = math.atanh(r)
        variance = 1 / (n - 3)
        rows.append({**study, "z": z, "variance": variance})

    weights = [1 / row["variance"] for row in rows]
    fixed_z = sum(w * row["z"] for w, row in zip(weights, rows)) / sum(weights)
    q = sum(w * (row["z"] - fixed_z) ** 2 for w, row in zip(weights, rows))
    df = len(rows) - 1
    c = sum(weights) - sum(w * w for w in weights) / sum(weights)
    tau_squared = max(0.0, (q - df) / c)
    i_squared = max(0.0, (q - df) / q) * 100 if q > 0 else 0.0
    critical = NormalDist().inv_cdf(0.975)

    def summary(pooled_weights: list[float]) -> dict:
        z = sum(w * row["z"] for w, row in zip(pooled_weights, rows)) / sum(pooled_weights)
        se = math.sqrt(1 / sum(pooled_weights))
        return {"r": math.tanh(z), "ci_low": math.tanh(z - critical * se),
                "ci_high": math.tanh(z + critical * se), "z": z, "se": se}

    forest = [{"extraction_id": row["extraction_id"], "source_id": row["source_id"],
               "r": row["r"], "n": row["n"],
               "ci_low": math.tanh(row["z"] - critical * math.sqrt(row["variance"])),
               "ci_high": math.tanh(row["z"] + critical * math.sqrt(row["variance"]))}
              for row in rows]
    return {"k": len(rows), "scale": "Fisher z (displayed as r)", "fixed": summary(weights),
            "random": summary([1 / (row["variance"] + tau_squared) for row in rows]),
            "heterogeneity": {"q": q, "df": df, "tau_squared": tau_squared, "i_squared_percent": i_squared},
            "forest": forest}
