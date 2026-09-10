from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats


MAX_ANALYSIS_ROWS = 100_000


def _json(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if pd.isna(value):
        return None
    return value


def _frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=columns).replace({None: np.nan})
    if len(frame) > MAX_ANALYSIS_ROWS:
        raise ValueError(f"Dataset exceeds the {MAX_ANALYSIS_ROWS:,}-row analysis limit.")
    return frame


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Unknown column(s): {', '.join(missing)}")
    result = frame[columns].apply(pd.to_numeric, errors="coerce").dropna()
    if result.empty:
        raise ValueError("The selected columns contain no complete numeric observations.")
    return result


def _columns_from_prompt(prompt: str, frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("outcome", "group", "predictors", "column", "columns", "value", "by"):
        match = re.search(rf"\b{key}\s*[:=]\s*([^;\n]+)", prompt, re.I)
        if match:
            values = [item.strip().strip("`'\" .,;") for item in match.group(1).split(",") if item.strip()]
            result[key] = values if key in {"predictors", "columns"} else values[0]
    mentioned = [column for column in frame.columns if re.search(rf"\b{re.escape(column)}\b", prompt, re.I)]
    result["mentioned"] = mentioned
    return result


def _descriptive(frame: pd.DataFrame, columns: list[str]) -> dict[str, Any]:
    data = _numeric(frame, columns)
    output = {}
    for column in columns:
        series = data[column]
        output[column] = {
            "count": int(series.count()),
            "missing": int(frame[column].isna().sum()),
            "mean": _json(series.mean()),
            "median": _json(series.median()),
            "std": _json(series.std()),
            "variance": _json(series.var()),
            "min": _json(series.min()),
            "q1": _json(series.quantile(0.25)),
            "q3": _json(series.quantile(0.75)),
            "max": _json(series.max()),
            "skewness": _json(series.skew()),
            "kurtosis": _json(series.kurt()),
        }
    return {"operation": "descriptive", "statistics": output}


def _oaxaca(frame: pd.DataFrame, outcome: str, group: str, predictors: list[str]) -> dict[str, Any]:
    selected = _numeric(frame, [outcome, group, *predictors])
    selected = selected[selected[group].isin([0, 1])]
    if selected[group].nunique() != 2:
        raise ValueError(f"Group column '{group}' must contain both 0 and 1.")
    samples = {value: selected[selected[group] == value] for value in (0, 1)}
    if min(len(samples[0]), len(samples[1])) <= len(predictors):
        raise ValueError("Each Oaxaca group needs more observations than predictors.")
    x0 = sm.add_constant(samples[0][predictors], has_constant="add")
    x1 = sm.add_constant(samples[1][predictors], has_constant="add")
    model0 = sm.OLS(samples[0][outcome], x0).fit()
    model1 = sm.OLS(samples[1][outcome], x1).fit()
    means0 = x0.mean()
    means1 = x1.mean()
    explained = float((means1 - means0).dot(model0.params))
    unexplained = float(means0.dot(model1.params - model0.params))
    gap = float(samples[1][outcome].mean() - samples[0][outcome].mean())
    return {
        "operation": "oaxaca_blinder",
        "outcome": outcome,
        "group": group,
        "reference_group": 0,
        "comparison_group": 1,
        "predictors": predictors,
        "observations": {"group_0": len(samples[0]), "group_1": len(samples[1])},
        "mean_outcome": {"group_0": _json(samples[0][outcome].mean()), "group_1": _json(samples[1][outcome].mean())},
        "gap": gap,
        "explained": explained,
        "unexplained": unexplained,
        "explained_share": explained / gap if gap else None,
        "unexplained_share": unexplained / gap if gap else None,
        "group_0_model": {"r_squared": _json(model0.rsquared), "coefficients": {k: _json(v) for k, v in model0.params.items()}},
        "group_1_model": {"r_squared": _json(model1.rsquared), "coefficients": {k: _json(v) for k, v in model1.params.items()}},
    }


def run_analysis(prompt: str, rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    frame = _frame(rows, columns)
    parsed = _columns_from_prompt(prompt, frame)
    lowered = prompt.lower()
    mentioned = parsed["mentioned"]
    numeric_columns = frame.select_dtypes(include="number").columns.tolist()
    selected = parsed.get("columns") or mentioned or numeric_columns
    if isinstance(selected, str):
        selected = [selected]

    if "oaxaca" in lowered or "blinder" in lowered:
        outcome = parsed.get("outcome") or (mentioned[0] if mentioned else None)
        group = parsed.get("group") or (mentioned[1] if len(mentioned) > 1 else None)
        predictors = parsed.get("predictors") or [column for column in numeric_columns if column not in {outcome, group}]
        if not outcome or not group or not predictors:
            raise ValueError("Oaxaca-Blinder requires outcome=..., group=..., and predictors=... in the prompt.")
        return _oaxaca(frame, outcome, group, predictors)

    if any(word in lowered for word in ("correlation", "correlate", "association")):
        data = _numeric(frame, selected)
        return {"operation": "correlation", "columns": selected, "pearson": data.corr(method="pearson").round(6).to_dict(), "spearman": data.corr(method="spearman").round(6).to_dict()}
    if "t-test" in lowered or "ttest" in lowered or "t test" in lowered:
        if len(selected) < 2:
            raise ValueError("A t-test requires two numeric columns, e.g. columns=before, after.")
        left, right = _numeric(frame, selected[:2]).iloc[:, 0], _numeric(frame, selected[:2]).iloc[:, 1]
        result = stats.ttest_rel(left, right, nan_policy="omit")
        return {"operation": "paired_t_test", "columns": selected[:2], "t_statistic": _json(result.statistic), "p_value": _json(result.pvalue), "n": int(min(left.count(), right.count()))}
    if "frequency" in lowered or "frequencies" in lowered or "distribution" in lowered:
        column = parsed.get("column") or (mentioned[0] if mentioned else frame.columns[0])
        counts = frame[column].value_counts(dropna=False)
        return {"operation": "frequency", "column": column, "counts": {str(_json(key)): int(value) for key, value in counts.items()}}
    if "regression" in lowered or "ols" in lowered:
        outcome = parsed.get("outcome") or (mentioned[0] if mentioned else None)
        predictors = parsed.get("predictors") or [column for column in selected if column != outcome]
        if not outcome or not predictors:
            raise ValueError("Regression requires outcome=... and predictors=... in the prompt.")
        data = _numeric(frame, [outcome, *predictors])
        model = sm.OLS(data[outcome], sm.add_constant(data[predictors], has_constant="add")).fit()
        return {"operation": "ols_regression", "outcome": outcome, "predictors": predictors, "n": int(model.nobs), "r_squared": _json(model.rsquared), "adjusted_r_squared": _json(model.rsquared_adj), "coefficients": {key: {"estimate": _json(value), "p_value": _json(model.pvalues[key]), "std_error": _json(model.bse[key])} for key, value in model.params.items()}}
    if any(word in lowered for word in ("missing", "null", "outlier")):
        result = {"operation": "data_quality", "rows": len(frame), "missing": {column: int(frame[column].isna().sum()) for column in frame.columns}}
        if "outlier" in lowered:
            numeric = frame.select_dtypes(include="number")
            result["outliers_iqr"] = {column: int(((numeric[column] < numeric[column].quantile(.25) - 1.5 * (numeric[column].quantile(.75) - numeric[column].quantile(.25))) | (numeric[column] > numeric[column].quantile(.75) + 1.5 * (numeric[column].quantile(.75) - numeric[column].quantile(.25)))).sum()) for column in numeric.columns}
        return result
    return _descriptive(frame, selected)
