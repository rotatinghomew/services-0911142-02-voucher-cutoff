"""门槛附近的局部估计与识别检查。

模块只在服务端计算，前端不另算统计答案。输入始终来自不可变数据版本：
年龄取 decision_vintage 之前可见的最后版本，结局只使用已成熟的随访记录。
结果把资格带来的变化（ITT）、门槛处实际核销跃迁（first stage）和核销的
局部效应（LATE）分开，并同时返回有效样本、门槛附近分布、登记密度与
协变量平衡检查。识别条件不满足时不输出肯定的核销效应。
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats
from statsmodels.discrete.discrete_model import Poisson

DEFAULTS = {"window": 90, "donut": 0, "order": 1, "kernel": "triangular"}
KERNEL_LABELS = {"triangular": "三角核", "uniform": "均匀核"}
RULE_LABELS = {
    "strict_less": "申请当日年龄严格小于门槛",
    "less_equal": "申请当日年龄小于等于门槛",
    "unknown": "未识别规则，按严格小于门槛",
}
STATUS_LABELS = {
    "identified": "识别条件通过",
    "weak_first_stage": "核销变化偏弱，只报告资格变化",
    "density_discontinuity": "门槛处登记密度不连续",
    "covariate_imbalance": "门槛两侧协变量不平衡",
    "differential_followup": "门槛两侧随访成熟度不平衡",
    "insufficient_support": "有效样本不足",
    "no_support": "窗口内没有可用样本",
}
CRITICAL_STATUSES = ("density_discontinuity", "covariate_imbalance", "differential_followup")
MIN_SIDE = {1: 20, 2: 30}
MIN_DISTINCT = 5
MIN_EFFECTIVE = 10
WEAK_F_STATISTIC = 10.0
MIN_FIRST_STAGE = 0.05
DENSITY_ALPHA = 0.05
MATURITY_ALPHA = 0.05


def policy_rule(text):
    compact = str(text or "").replace(" ", "").lower()
    if "<=" in compact or "≤" in compact:
        return "less_equal"
    if "<" in compact:
        return "strict_less"
    return "unknown"


def centre_shift(rule):
    # 严格小于时门槛落在 cutoff-1 与 cutoff 之间；小于等于时落在 cutoff 与 cutoff+1 之间。
    return 0.5 if rule != "less_equal" else -0.5


def _integer(selection, key, default, low, high):
    value = selection.get(key, default)
    if isinstance(value, bool):
        raise ValueError("分析参数应为整数：" + key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("分析参数应为整数：" + key) from None
    if not math.isfinite(number) or number != int(number):
        raise ValueError("分析参数应为整数：" + key)
    number = int(number)
    if number < low or number > high:
        raise ValueError(f"分析参数超出范围（{low}至{high}）：{key}")
    return number


def normalize_selection(selection, policy):
    if not isinstance(selection, dict):
        raise ValueError("门槛分析选择必须是对象")
    year = str(selection.get("year", policy["year"]))
    if year != str(policy["year"]):
        raise ValueError("年度与政策不匹配")
    rule = policy_rule(policy.get("eligibility"))
    kernel = selection.get("kernel", DEFAULTS["kernel"])
    options = {
        "window": _integer(selection, "window", DEFAULTS["window"], 5, 365),
        "donut": _integer(selection, "donut", DEFAULTS["donut"], 0, 365),
        "followup": _integer(selection, "followup", policy["followup_days"], 1, 365),
        "order": _integer(selection, "order", DEFAULTS["order"], 1, 2),
        "kernel": kernel if isinstance(kernel, str) else "",
    }
    if options["donut"] >= options["window"]:
        raise ValueError("边界排除范围必须小于分析窗口")
    if options["followup"] < policy["followup_days"]:
        raise ValueError("随访阈值不能低于政策规定的结局随访天数")
    if options["kernel"] not in KERNEL_LABELS:
        raise ValueError("核函数应为 triangular 或 uniform")
    return {"year": year, **options, "rule": rule, "shift": centre_shift(rule)}


def _versions(age_history):
    versions = {}
    for row in age_history:
        versions.setdefault(row["application"], []).append(row)
    return versions


def _latest(rows):
    return max(rows, key=lambda row: (row["visible_at"], str(row["change"])))


def prepare(data, selection):
    """按年度规则、资料截面和随访成熟度整理分析样本。"""
    policies = {str(row["year"]): row for row in data["policies"]}
    year = str((selection or {}).get("year", ""))
    if not year and policies:
        year = sorted(policies)[-1]
    if year not in policies:
        raise ValueError("补贴年度没有对应政策资料：" + year)
    policy = policies[year]
    options = normalize_selection(selection, policy)
    cutoff = policy["cutoff_days"]
    vintage = policy["decision_vintage"]
    data_cutoff = policy["data_cutoff"]
    shift = options["shift"]
    versions = _versions(data["age_history"])
    exclusions = {
        "records_total": 0,
        "missing_age_history": 0,
        "age_not_visible_at_vintage": 0,
        "outside_window": 0,
        "donut_excluded": 0,
        "not_recorded_at_cutoff": 0,
        "immature_null_outcome": 0,
        "immature_short_followup": 0,
        "included": 0,
        "later_correction": 0,
        "vintage_age_differs_from_latest": 0,
        "cutoff_equality_count": 0,
    }
    records = []
    for row in data["applications"]:
        if str(row["year"]) != year:
            continue
        exclusions["records_total"] += 1
        history = versions.get(row["application"], [])
        if not history:
            exclusions["missing_age_history"] += 1
            continue
        visible = [item for item in history if item["visible_at"] <= vintage]
        if not visible:
            exclusions["age_not_visible_at_vintage"] += 1
            continue
        used = _latest(visible)
        latest = _latest(history)
        if any(item["visible_at"] > vintage for item in history):
            exclusions["later_correction"] += 1
        if latest["age_days"] != used["age_days"]:
            exclusions["vintage_age_differs_from_latest"] += 1
        age = used["age_days"]
        distance = age - cutoff
        x = distance + shift
        if age == cutoff:
            exclusions["cutoff_equality_count"] += 1
        in_window = abs(x) <= options["window"]
        in_donut = abs(x) <= options["donut"]
        at_cutoff = row["recorded_at"] <= data_cutoff
        mature = row["visit_60d"] is not None and row["observed_days"] >= options["followup"] and at_cutoff
        record = {
            "application": row["application"],
            "age_days": age,
            "latest_age_days": latest["age_days"],
            "visible_at": used["visible_at"],
            "distance": distance,
            "x": x,
            "eligible": x < 0,
            "voucher": 1.0 if row["voucher_used"] else 0.0,
            "outcome": float(row["visit_60d"]) if row["visit_60d"] is not None else None,
            "observed_days": row["observed_days"],
            "income_band": float(row["income_band"]),
            "applied_day": float(row["applied_day"]),
            "recorded_at": float(row["recorded_at"]),
            "registration_lag": float(row["recorded_at"] - row["applied_day"]),
            "center": str(row["center"]),
            "at_cutoff": at_cutoff,
            "mature": mature,
            "in_window": in_window,
            "in_donut": in_donut,
            "included": in_window and not in_donut and mature,
        }
        if not in_window:
            exclusions["outside_window"] += 1
        elif in_donut:
            exclusions["donut_excluded"] += 1
        elif not at_cutoff:
            exclusions["not_recorded_at_cutoff"] += 1
        elif not mature:
            if row["visit_60d"] is None:
                exclusions["immature_null_outcome"] += 1
            else:
                exclusions["immature_short_followup"] += 1
        else:
            exclusions["included"] += 1
        records.append(record)

    in_window = [row for row in records if row["in_window"]]
    included = [row for row in records if row["included"]]
    exclusions["eligible_n"] = sum(1 for row in included if row["eligible"])
    exclusions["ineligible_n"] = len(included) - exclusions["eligible_n"]
    exclusions["voucher_users"] = sum(1 for row in included if row["voucher"] == 1)
    exclusions["corrected_before_vintage"] = sum(1 for row in records if row["visible_at"] > 1)
    return {
        "policy": policy,
        "options": options,
        "rule": {
            "code": options["rule"],
            "label": RULE_LABELS[options["rule"]],
            "shift": shift,
            "boundary_equality_is_eligible": options["rule"] == "less_equal",
        },
        "exclusions": exclusions,
        "records": records,
        "in_window": in_window,
        "included": included,
        "vintage": vintage,
        "cutoff": cutoff,
        "data_cutoff": data_cutoff,
    }


def _design(x, eligible, order):
    columns = [np.ones_like(x), eligible.astype(float)]
    for power in range(1, order + 1):
        columns.append(x ** power)
        columns.append(eligible * x ** power)
    return np.column_stack(columns)


def _weights(x, window, kernel):
    if kernel == "triangular":
        return np.clip(1.0 - np.abs(x) / window, 0.0, None)
    return np.ones_like(x)


def _fit(x, y, eligible, weights, order):
    matrix = _design(x, eligible, order)
    n, k = matrix.shape
    gram = matrix.T @ (matrix * weights[:, None])
    inverse = np.linalg.pinv(gram)
    beta = inverse @ (matrix.T @ (weights * y))
    residual = y - matrix @ beta
    meat = matrix.T @ (matrix * ((weights * residual) ** 2)[:, None])
    covariance = inverse @ meat @ inverse * n / max(n - k, 1)
    return beta, covariance, residual, inverse


def _finite(value, digits=6):
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def _interval(estimate, variance, degrees):
    if estimate is None or variance is None or not math.isfinite(variance) or variance <= 0:
        return [None, None]
    critical = float(stats.t.ppf(0.975, max(degrees, 1)))
    half = critical * math.sqrt(variance)
    return [_finite(estimate - half), _finite(estimate + half)]


def _equivalent_n(weights, eligible):
    result = {}
    for name, mask in (("left", eligible == 1), ("right", eligible == 0)):
        side = weights[mask]
        denominator = float((side ** 2).sum())
        result[name] = float(side.sum() ** 2 / denominator) if denominator > 0 else 0.0
    result["total"] = result["left"] + result["right"]
    return result


def _joint_estimate(x, outcome, treatment, eligible, weights, order):
    """Jointly estimate the outcome and treatment jumps with one design.

    The two equations share the same regressors and weights. The sandwich
    covariance retains the cross-equation moment so the delta-method interval
    for LATE accounts for the correlation between first stage and reduced form.
    """
    matrix = _design(x, eligible, order)
    n, k = matrix.shape
    gram = matrix.T @ (matrix * weights[:, None])
    inverse = np.linalg.pinv(gram)
    beta_y = inverse @ (matrix.T @ (weights * outcome))
    beta_d = inverse @ (matrix.T @ (weights * treatment))
    residual_y = outcome - matrix @ beta_y
    residual_d = treatment - matrix @ beta_d
    scale = n / max(n - k, 1)
    block_yy = matrix.T @ (matrix * ((weights * residual_y) ** 2)[:, None]) * scale
    block_dd = matrix.T @ (matrix * ((weights * residual_d) ** 2)[:, None]) * scale
    block_yd = matrix.T @ (matrix * (weights ** 2 * residual_y * residual_d)[:, None]) * scale
    cov_yy = inverse @ block_yy @ inverse
    cov_dd = inverse @ block_dd @ inverse
    cov_yd = inverse @ block_yd @ inverse
    return {
        "jump_outcome": float(beta_y[1]),
        "jump_treatment": float(beta_d[1]),
        "var_outcome": float(cov_yy[1, 1]),
        "var_treatment": float(cov_dd[1, 1]),
        "cov": float(cov_yd[1, 1]),
        "degrees": n - 2 * k,
        "n": int(n),
    }


DENSITY_SPAN = 60.0


def _density(x, window, donut):
    # 密度检验使用固定近门槛范围，不随分析窗口放大，避免把远端支撑边界误读为操纵。
    span = min(float(window - donut), DENSITY_SPAN)
    width = max(10.0, min(15.0, 2 * span / 12))
    edges = np.arange(-span, span + width, width)
    counts, _ = np.histogram(x, bins=edges)
    centres = (edges[:-1] + edges[1:]) / 2
    return counts.astype(float), centres, width


def density_test(x, window, donut):
    """Poisson continuity test for the running variable near the cutoff.

    Counts are modelled as exp(a + b*side + c*centre + d*side*centre) with a
    log-width offset. The joint Wald statistic on b and d tests whether the
    registration density jumps at the cutoff. Overdispersion is reported but
    does not by itself move the interval. Records inside a donut are omitted,
    matching the estimation sample.
    """
    span = min(float(window - donut), DENSITY_SPAN)
    if donut:
        x = x[np.abs(x) > donut]
    counts, centres, width = _density(x, window, donut)
    side = (centres < 0).astype(float)
    left_count = int(counts[side == 1].sum())
    right_count = int(counts[side == 0].sum())
    if left_count < 10 or right_count < 10:
        return {
            "available": False,
            "left_count": left_count,
            "right_count": right_count,
            "reason": "门槛附近任一侧登记数过少，无法检验密度连续性",
            "counts": [int(value) for value in counts],
            "centres": [_finite(value) for value in centres],
            "bin_width": _finite(width),
        }
    matrix = np.column_stack([np.ones_like(centres), side, centres, side * centres])
    try:
        model = Poisson(counts, matrix, offset=np.full(len(counts), math.log(width))).fit(disp=0, maxiter=200)
        beta = np.asarray(model.params)[[1, 3]]
        covariance = np.asarray(model.cov_params())[np.ix_([1, 3], [1, 3])]
        statistic = float(beta @ np.linalg.solve(covariance, beta))
        pvalue = float(stats.chi2.sf(statistic, 2))
        fitted = np.asarray(model.predict())
    except Exception:
        return {
            "available": False,
            "left_count": left_count,
            "right_count": right_count,
            "reason": "密度模型未能收敛",
            "counts": [int(value) for value in counts],
            "centres": [_finite(value) for value in centres],
            "bin_width": _finite(width),
        }
    pearson = float(((counts - fitted) ** 2 / np.maximum(fitted, 1e-9)).sum() / max(len(counts) - 4, 1))
    return {
        "available": True,
        "left_count": left_count,
        "right_count": right_count,
        "left_rate": _finite(left_count / max((side == 1).sum() * width, 1e-9)),
        "right_rate": _finite(right_count / max((side == 0).sum() * width, 1e-9)),
        "statistic": _finite(statistic),
        "p_value": _finite(pvalue),
        "overdispersion": _finite(pearson),
        "span": _finite(span),
        "counts": [int(value) for value in counts],
        "centres": [_finite(value) for value in centres],
        "bin_width": _finite(width),
    }


def _jump_test(x, y, eligible, weights, order):
    if float(np.var(y)) <= 1e-12:
        return 0.0, None, None
    beta, covariance, _, _ = _fit(x, y, eligible, weights, order)
    degrees = len(x) - 2 * (order + 1)
    variance = float(covariance[1, 1])
    if not math.isfinite(variance) or variance <= 1e-12:
        return float(beta[1]), None, None
    pvalue = float(2 * stats.t.sf(abs(beta[1] / math.sqrt(variance)), max(degrees, 1)))
    return float(beta[1]), variance, pvalue


def covariate_checks(records, x, eligible, weights, order):
    checks = []
    for key, label in (("income_band", "收入档"), ("applied_day", "申请日"),
                       ("registration_lag", "登记滞后（记录日-申请日）")):
        y = np.array([row[key] for row in records], dtype=float)
        jump, variance, pvalue = _jump_test(x, y, eligible, weights, order)
        checks.append({"field": key, "label": label, "jump": _finite(jump), "p_value": _finite(pvalue),
                       "constant": variance is None})
    for centre in sorted({row["center"] for row in records}):
        y = np.array([1.0 if row["center"] == centre else 0.0 for row in records])
        jump, variance, pvalue = _jump_test(x, y, eligible, weights, order)
        checks.append({"field": "center:" + centre, "label": "中心占比 " + centre, "jump": _finite(jump),
                       "p_value": _finite(pvalue), "constant": variance is None})
    return checks


def maturity_check(records, window, donut):
    usable = [row for row in records if abs(row["x"]) <= window and abs(row["x"]) > donut]
    if len(usable) < 2 * MIN_SIDE[1]:
        return None
    x = np.array([row["x"] for row in usable])
    y = np.array([1.0 if row["mature"] else 0.0 for row in usable])
    eligible = (x < 0).astype(float)
    weights = _weights(x, window, "triangular")
    jump, variance, pvalue = _jump_test(x, y, eligible, weights, 1)
    return {
        "n": len(usable),
        "left_share": _finite(float(y[eligible == 1].mean())) if (eligible == 1).any() else None,
        "right_share": _finite(float(y[eligible == 0].mean())) if (eligible == 0).any() else None,
        "jump": _finite(jump),
        "p_value": _finite(pvalue),
        "null_share": _finite(float(np.mean([row["outcome"] is None for row in usable]))),
    }


def _naive_comparison(records):
    treated = [row["outcome"] for row in records if row["voucher"] == 1]
    untreated = [row["outcome"] for row in records if row["voucher"] == 0]
    left = [row for row in records if row["eligible"]]
    right = [row for row in records if not row["eligible"]]
    return {
        "voucher_users": len(treated),
        "voucher_nonusers": len(untreated),
        "voucher_outcome": _finite(sum(treated) / len(treated)) if treated else None,
        "nonvoucher_outcome": _finite(sum(untreated) / len(untreated)) if untreated else None,
        "difference": _finite(sum(treated) / len(treated) - sum(untreated) / len(untreated))
        if treated and untreated else None,
        "eligible_n": len(left),
        "ineligible_n": len(right),
        "eligible_outcome": _finite(sum(row["outcome"] for row in left) / len(left)) if left else None,
        "ineligible_outcome": _finite(sum(row["outcome"] for row in right) / len(right)) if right else None,
        "eligible_voucher_share": _finite(sum(row["voucher"] for row in left) / len(left)) if left else None,
        "ineligible_voucher_share": _finite(sum(row["voucher"] for row in right) / len(right)) if right else None,
    }


def _histograms(records, window, donut, width=None):
    span = window - donut
    width = width or max(5.0, min(15.0, 2 * span / 18))
    edges = np.arange(-span, span + width, width)
    centres = (edges[:-1] + edges[1:]) / 2
    entries = []
    for left_edge, centre in zip(edges[:-1], centres):
        rows = [row for row in records if left_edge <= row["x"] < left_edge + width]
        vouchers = sum(row["voucher"] for row in rows)
        matured = [row for row in rows if row["outcome"] is not None]
        visits = sum(row["outcome"] for row in matured)
        entries.append({
            "centre": _finite(centre),
            "left": _finite(left_edge),
            "width": _finite(width),
            "n": len(rows),
            "voucher": int(round(vouchers)),
            "mature": len(matured),
            "visits": int(round(visits)),
        })
    return entries


def _bins_for_plot(x, window, donut, width=None):
    span = window - donut
    width = width or max(10.0, min(30.0, 2 * span / 12))
    edges = np.arange(-span, span + width, width)
    entries = []
    for left_edge in edges[:-1]:
        entries.append({
            "left": _finite(left_edge),
            "right": _finite(min(left_edge + width, span)),
            "centre": _finite(left_edge + width / 2),
            "n": int(((x >= left_edge) & (x < left_edge + width)).sum()),
        })
    return entries


def _descriptive(records, prepared):
    left = [row for row in records if row["eligible"]]
    right = [row for row in records if not row["eligible"]]
    def side(rows):
        if not rows:
            return {"n": 0, "mature_n": 0, "voucher_share": None, "visit_share": None}
        matured = [row for row in rows if row["outcome"] is not None]
        return {
            "n": len(rows),
            "mature_n": len(matured),
            "voucher_share": _finite(sum(row["voucher"] for row in rows) / len(rows)),
            "visit_share": _finite(sum(row["outcome"] for row in matured) / len(matured)) if matured else None,
            "median_age": _finite(float(np.median([row["age_days"] for row in rows]))),
        }
    return {
        "note": "这些是描述值，未经门槛识别，不能读作补贴效果。",
        "left": side(left),
        "right": side(right),
        "whole_window": side(records),
        "cutoff_days": prepared["cutoff"],
        "rule": prepared["rule"]["label"],
        "vintage": prepared["vintage"],
        "data_cutoff": prepared["data_cutoff"],
    }


def estimate(data, selection):
    prepared = prepare(data, selection)
    options = prepared["options"]
    records = prepared["included"]
    window = options["window"]
    donut = options["donut"]
    order = options["order"]
    kernel = options["kernel"]
    rule = prepared["rule"]
    exclusions = prepared["exclusions"]
    notes = []
    warnings = []

    summary = {
        "year": prepared["policy"]["year"],
        "rule": rule,
        "cutoff_days": prepared["cutoff"],
        "vintage": prepared["vintage"],
        "data_cutoff": prepared["data_cutoff"],
        "window": window,
        "donut": donut,
        "followup": options["followup"],
        "order": order,
        "kernel": kernel,
        "kernel_label": KERNEL_LABELS[kernel],
        "included_n": len(records),
        "interval_level": 0.95,
        "covariance": "HC1 sandwich，联合估计 reduced form 与 first stage，LATE 用 delta 法",
    }
    in_window_n = len(prepared["in_window"])
    left_n = sum(1 for row in records if row["eligible"])
    right_n = len(records) - left_n
    summary.update({"window_records": in_window_n, "left_n": left_n, "right_n": right_n})
    if rule["code"] == "unknown":
        warnings.append("政策未明确写出边界等号，按严格小于纳入；边界样本的资格状态需业务确认。")
    if exclusions["cutoff_equality_count"]:
        warnings.append(
            f"资料中有 {exclusions['cutoff_equality_count']} 条申请当日年龄恰等于门槛；"
            + ("这些记录按现行规则视为有资格。" if rule["boundary_equality_is_eligible"] else "这些记录按现行规则视为不具资格。")
        )

    support = {
        "eligible_n": left_n,
        "ineligible_n": right_n,
        "required_per_side": MIN_SIDE[order],
        "distinct_x": int(len({round(row["x"], 6) for row in records})),
        "effective_n": None,
        "bandwidth": window,
        "donut": donut,
    }

    if not records:
        status = "no_support"
        summary["status"] = status
        summary["status_label"] = STATUS_LABELS[status]
        notes.append("窗口内没有同时满足资料截面与随访成熟度的记录，不能给出门槛估计。")
        return {
            "summary": summary,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "identified": False,
            "support": support,
            "exclusions": exclusions,
            "estimates": [],
            "diagnostics": {},
            "covariates": [],
            "bins": _bins_for_plot(np.array([]), window, donut),
            "histograms": [],
            "naive": {},
            "descriptive": _descriptive(prepared["in_window"], prepared),
            "notes": notes,
            "warnings": warnings,
        }

    x = np.array([row["x"] for row in records], dtype=float)
    outcome = np.array([row["outcome"] for row in records], dtype=float)
    treatment = np.array([row["voucher"] for row in records], dtype=float)
    eligible = (x < 0).astype(float)
    weights = _weights(x, window, kernel)
    equivalent = _equivalent_n(weights, eligible)
    support["effective_n"] = _finite(equivalent["total"])
    support["effective_left_n"] = _finite(equivalent["left"])
    support["effective_right_n"] = _finite(equivalent["right"])
    support["voucher_n"] = int(round(treatment.sum()))
    support["nonvoucher_n"] = int(len(treatment) - round(treatment.sum()))
    support["outcome_ones"] = int(round(outcome.sum()))

    def usable():
        return (left_n >= MIN_SIDE[order] and right_n >= MIN_SIDE[order]
                and support["distinct_x"] >= MIN_DISTINCT
                and equivalent["total"] >= MIN_EFFECTIVE)

    window_x = np.array([row["x"] for row in prepared["in_window"]], dtype=float)
    density = density_test(window_x, window, donut)
    matures = maturity_check(prepared["in_window"], window, donut)
    diagnostics = {"density": density, "maturity": matures, "outcome_min_cell": None,
                   "vintage": {
                       "decision_vintage": prepared["vintage"],
                       "data_cutoff": prepared["data_cutoff"],
                       "records_with_later_correction": exclusions["later_correction"],
                       "latest_age_differs": exclusions["vintage_age_differs_from_latest"],
                       "eligibility_would_change_with_latest": sum(
                           1 for row in prepared["records"]
                           if (row["latest_age_days"] < prepared["cutoff"]) != (row["age_days"] < prepared["cutoff"])
                       ),
                   }}

    def integrity():
        problems = []
        issues = []
        if not usable():
            return "insufficient_support", problems, issues
        if density.get("available") and density.get("p_value") is not None and density["p_value"] < DENSITY_ALPHA:
            problems.append("density_discontinuity")
            issues.append("门槛处登记密度存在统计不连续；两侧人口不具可比性。")
        if matures and matures.get("p_value") is not None and matures["p_value"] < MATURITY_ALPHA:
            problems.append("differential_followup")
            issues.append("门槛两侧随访成熟度不同；结局样本不是同一风险集合。")
        tested = [check for check in covariates if not check.get("constant") and check["p_value"] is not None]
        imbalance = [check for check in tested
                     if check["p_value"] < 0.05 / max(len(tested), 1)]
        if imbalance:
            problems.append("covariate_imbalance")
            issues.append("以下门槛前协变量出现不连续：" + "、".join(check["label"] for check in imbalance))
        marginal = [check for check in tested if check not in imbalance and check["p_value"] < 0.05]
        if marginal:
            warnings.append("以下协变量单独看p<0.05、未通过多重比较校正："
                            + "、".join(check["label"] for check in marginal) + "；解释时留意。")
        return (problems[0] if problems else "identified"), problems, issues

    balance_records = [row for row in prepared["in_window"] if not row["in_donut"]]
    balance_x = np.array([row["x"] for row in balance_records], dtype=float)
    balance_eligible = (balance_x < 0).astype(float)
    balance_weights = _weights(balance_x, window, kernel)
    covariates = covariate_checks(balance_records, balance_x, balance_eligible, balance_weights, order)
    joint = _joint_estimate(x, outcome, treatment, eligible, weights, order)
    diagnostics["outcome_min_cell"] = {
        "treated_ones": int(round(outcome[treatment == 1].sum())),
        "treated_n": int(round((treatment == 1).sum())),
        "untreated_ones": int(round(outcome[treatment == 0].sum())),
        "untreated_n": int(round((treatment == 0).sum())),
        "eligible_ones": int(round(outcome[eligible == 1].sum())),
        "eligible_n": int(round((eligible == 1).sum())),
        "ineligible_ones": int(round(outcome[eligible == 0].sum())),
        "ineligible_n": int(round((eligible == 0).sum())),
    }

    first_jump = joint["jump_treatment"]
    first_var = joint["var_treatment"]
    first_f = first_jump ** 2 / first_var if first_var > 0 else None
    diagnostics["first_stage"] = {
        "jump": _finite(first_jump),
        "interval": _interval(first_jump, first_var, joint["degrees"]),
        "f_statistic": _finite(first_f),
        "weak": first_f is None or first_f < WEAK_F_STATISTIC,
    }

    outcome_jump = joint["jump_outcome"]
    outcome_var = joint["var_outcome"]
    late_var = joint["var_treatment"]
    late_cov = joint["cov"]
    late_interval = None

    estimates = [
        {
            "id": "itt_eligibility",
            "label": "资格带来的在园结局变化（ITT，门槛处跃迁）",
            "estimate": _finite(outcome_jump),
            "interval": _interval(outcome_jump, outcome_var, joint["degrees"]),
            "n": joint["n"],
            "note": "按申请当日年龄规则比较门槛两侧；与实际核销无关，只回答“有资格”带来的变化。",
        },
        {
            "id": "first_stage_voucher",
            "label": "门槛处实际核销跃迁（first stage）",
            "estimate": _finite(first_jump),
            "interval": _interval(first_jump, first_var, joint["degrees"]),
            "n": joint["n"],
            "note": "资格线两侧实际核销比例的差；不完整依从时这不是政策分配的全部效果。",
        },
    ]

    status, problems, issues = integrity()
    diagnostics["problems"] = problems
    notes.extend(issues)

    first_stage_strong = (first_jump >= MIN_FIRST_STAGE
                          and first_f is not None and first_f >= WEAK_F_STATISTIC)
    if status in CRITICAL_STATUSES:
        for item in estimates:
            if item["id"] == "first_stage_voucher":
                item["note"] = "门槛处实际核销比例的差，仅作描述；识别检查未通过，不能据此换算效应。"
                continue
            item["estimate"] = None
            item["interval"] = None
            item["direction"] = "undetermined"
            item["note"] = "识别检查未通过，门槛处的比较不具因果含义；不报告此项。"
        diagnostics["late"] = {
            "estimate": None,
            "interval": None,
            "reason": "识别检查未通过，门槛比值不具因果含义；不输出核销效应。",
        }
        notes.append(
            "仍可回答的描述性问题：窗口内两侧各有多少申请、实际核销比例、成熟随访中的在园比例、"
            "年龄距门槛的登记分布，以及哪些资料因截面、成熟度或边界被排除。"
        )
    elif first_jump < MIN_FIRST_STAGE:
        diagnostics["late"] = {
            "estimate": None,
            "interval": None,
            "reason": "资格线处实际核销没有上升，不能把资格变化换算成核销效应。",
        }
        if status == "identified":
            status = "weak_first_stage"
        notes.append("资格线处实际核销没有上升，需先核对核销记录与资格规则；当前只报告资格差异与描述分布。")
    elif not first_stage_strong:
        diagnostics["late"] = {
            "estimate": None,
            "interval": None,
            "reason": "门槛处实际核销跃迁偏小或工具偏弱，不能把资格变化换算成核销效应。",
        }
        if status == "identified":
            status = "weak_first_stage"
        notes.append(
            "核销在门槛处变化不足：仍可报告两侧在园结局的资格差异（ITT）、实际核销比例、门槛附近分布"
            "与协变量检查，但不能把两者的比值解释为补贴效果；可等待更多核销或改用其他识别设计。"
        )
    else:
        late = outcome_jump / first_jump
        variance = (outcome_var - 2 * late * late_cov + late ** 2 * late_var) / (first_jump ** 2)
        late_interval = _interval(late, max(variance, 0), joint["degrees"])
        diagnostics["late"] = {
            "estimate": _finite(late),
            "interval": late_interval,
            "first_stage_minimum": MIN_FIRST_STAGE,
        }

    for item in estimates:
        interval = item.get("interval") or [None, None]
        if item["estimate"] is None or interval[0] is None or interval[1] is None:
            item["direction"] = "undetermined"
        elif interval[0] > 0:
            item["direction"] = "positive"
        elif interval[1] < 0:
            item["direction"] = "negative"
        else:
            item["direction"] = "undetermined"

    if diagnostics["late"]["estimate"] is not None:
        estimates.append({
            "id": "late_voucher",
            "label": "实际核销的局部效应（LATE，以门槛处核销跃迁为尺度）",
            "estimate": diagnostics["late"]["estimate"],
            "interval": late_interval,
            "n": joint["n"],
            "note": "仅适用于门槛附近、且因门槛跨越而改变核销行为的家庭；不是全市平均效果。",
        })
    else:
        estimates.append({
            "id": "late_voucher",
            "label": "实际核销的局部效应（LATE）",
            "estimate": None,
            "interval": None,
            "n": joint["n"],
            "note": diagnostics["late"]["reason"],
        })

    late_item = next(item for item in estimates if item["id"] == "late_voucher")
    interval = late_item.get("interval") or [None, None]
    if late_item["estimate"] is None or interval[0] is None or interval[1] is None:
        late_item["direction"] = "undetermined"
    elif interval[0] > 0:
        late_item["direction"] = "positive"
    elif interval[1] < 0:
        late_item["direction"] = "negative"
    else:
        late_item["direction"] = "undetermined"

    if not usable():
        if status not in CRITICAL_STATUSES:
            status = "insufficient_support"
        for item in estimates:
            if item["id"] == "first_stage_voucher":
                item["estimate"] = None
                item["interval"] = None
                item["note"] = "样本不足，比例差也不稳定；只看门槛两侧描述。"
                continue
            item["estimate"] = None
            item["interval"] = None
            item["note"] = "有效样本不足，不报告估计。"
        diagnostics["late"] = {
            "estimate": None,
            "interval": None,
            "reason": "有效样本不足，不能形成核销效应估计。",
        }
        for item in estimates:
            item["direction"] = "undetermined"
        notes.append("门槛两侧或有效样本不足，估计不稳定，不报告比值；可扩大窗口或先补齐随访。")

    if status in CRITICAL_STATUSES:
        notes.append("识别检查未通过：上述比值不应作为补贴效果使用；可先核对登记流程与资料覆盖，或只报告原始描述。")
    elif status == "identified":
        notes.append(
            "解释依赖这些假设：门槛附近除资格外无其他同时跃迁；年龄测量在门槛处无系统性操纵；"
            "随访成熟与结局缺失不随门槛跳变。LATE 只覆盖因跨越门槛而改变核销行为的家庭。"
        )
    elif status == "insufficient_support":
        notes.append("样本不支持稳定估计；门槛附近分布与排除说明仍可用于核对资料。")
    if first_f is not None and first_f < WEAK_F_STATISTIC:
        warnings.append(f"first stage 的 F 统计量为 {_finite(first_f)}，低于常用参考值 {WEAK_F_STATISTIC}；核销工具偏弱。")

    if status == "identified":
        late_item = next((item for item in estimates if item["id"] == "late_voucher"), None)
        if late_item and late_item.get("direction") == "undetermined":
            notes.append("LATE 的95%区间包含0，现有资料不能支持明确的补贴效应方向，只能说明区间范围。")

    summary["status"] = status
    summary["status_label"] = STATUS_LABELS[status]

    late_item = next(item for item in estimates if item["id"] == "late_voucher")
    itt_item = next(item for item in estimates if item["id"] == "itt_eligibility")
    first_item = next(item for item in estimates if item["id"] == "first_stage_voucher")
    if status == "identified":
        direction = {"positive": "区间显示核销与在园结局同向上升", "negative": "区间显示核销与在园结局反向",
                     "undetermined": "区间跨过0，方向未定"}[late_item["direction"]]
        conclusion = (
            f"门槛处可识别的核销效应估计为 {late_item['estimate']:+.3f}（95%区间 "
            f"{late_item['interval'][0]:+.3f} 至 {late_item['interval'][1]:+.3f}，{direction}）。"
            f"资格本身的在园结局跃迁为 {itt_item['estimate']:+.3f}（区间 "
            f"{itt_item['interval'][0]:+.3f} 至 {itt_item['interval'][1]:+.3f}）；"
            f"实际核销比例跃迁为 {first_item['estimate']:+.3f}。"
        )
        scope = "结论只适用于门槛附近、因门槛跨越而改变核销行为的家庭，不是全市平均。"
    elif status == "weak_first_stage":
        conclusion = (
            f"实际核销比例在门槛处跃迁为 {first_item['estimate']:+.3f}，F 统计量 "
            f"{_finite(first_f)}，不足以支撑核销效应换算。"
            f"可以报告资格差异对在园结局的跃迁 {itt_item['estimate']:+.3f}（区间 "
            f"{itt_item['interval'][0]:+.3f} 至 {itt_item['interval'][1]:+.3f}）与两侧核销比例，"
            "但不输出“补贴使入托上升/下降多少”的结论。"
        )
        scope = "若需要核销效应，可扩大有效样本、核对核销登记，或改用其他识别设计。"
    elif status in CRITICAL_STATUSES:
        conclusion = (
            f"识别检查未通过（{STATUS_LABELS[status]}），门槛两侧不构成可比组，"
            "不能输出资格差异或核销效应；页面只保留原始描述与排除说明。"
        )
        scope = "可先核对登记流程、资料覆盖和年龄字段的测量口径，再重新评估。"
    else:
        conclusion = "有效样本不足，未形成可解释的门槛估计；只保留两侧描述与排除计数。"
        scope = "可扩大分析窗口、核对资料截面，或等待随访成熟。"

    response = {
        "conclusion": conclusion,
        "scope": scope,
    }
    return {
        "summary": summary,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "identified": status == "identified",
        "support": support,
        "exclusions": exclusions,
        "estimates": estimates,
        "diagnostics": diagnostics,
        "covariates": covariates,
        "covariate_sample_n": len(balance_records),
        "methods": {
            "estimator": "局部多项式（" + ("线性" if order == 1 else "二次") + "）门槛跃迁，按申请日年龄距门槛的中心化坐标",
            "kernel": KERNEL_LABELS[kernel] + "，带宽 " + str(window) + " 天",
            "donut": donut,
            "followup": options["followup"],
            "interval": "95% t 区间，HC1 稳健协方差；first stage 与结局联合估计，LATE 用 delta 法",
            "density": "固定近门槛范围（至多±60天）年龄分箱的 Poisson 连续性检验",
            "balance": "收入档、申请日、登记滞后与各中心占比的门槛跃迁检验，Bonferroni 校正",
            "maturity": "门槛两侧结局成熟比例的局部线性跃迁检验",
            "first_stage_rule": "跃迁≥" + str(MIN_FIRST_STAGE) + " 且 F≥" + str(WEAK_F_STATISTIC) + " 才换算 LATE",
        },
        "bins": _bins_for_plot(x, window, donut),
        "histograms": _histograms(records, window, donut),
        "naive": _naive_comparison(records),
        "naive_note": "核销家庭与未核销家庭的直接差是混合比较，含资格、年龄与登记差异，不是补贴效应。",
        "descriptive": _descriptive([row for row in prepared["in_window"] if not row["in_donut"]], prepared),
        "notes": notes,
        "warnings": warnings,
        "conclusion": response["conclusion"],
        "scope": response["scope"],
        "rows": [
            {key: row[key] for key in ("application", "age_days", "latest_age_days", "visible_at",
                                       "distance", "eligible", "voucher", "outcome", "observed_days",
                                       "income_band", "center")}
            for row in prepared["included"]
        ],
    }


def alternatives(data, selection):
    """Few plausible specifications, so dependence on assumptions is visible."""
    base = dict(selection)
    window = _integer(base, "window", DEFAULTS["window"], 5, 365)
    donut = _integer(base, "donut", DEFAULTS["donut"], 0, 365)
    policy = next((row for row in data["policies"] if str(row["year"]) == str(base.get("year", ""))), None)
    if policy is None:
        policy = sorted(data["policies"], key=lambda row: str(row["year"]))[-1]
        base["year"] = policy["year"]
    followup = _integer(base, "followup", policy["followup_days"], 1, 365)
    order = _integer(base, "order", DEFAULTS["order"], 1, 2)
    kernel = base.get("kernel", DEFAULTS["kernel"])
    variants = []
    definitions = [
        ("基准", {}),
        (f"窗口{max(5, window // 2)}天", {"window": max(5, window // 2)}),
        (f"窗口{min(365, max(window + 30, 120))}天", {"window": min(365, max(window + 30, 120))}),
        ("排除边界±2天", {"donut": 2}),
        ("改为二次" if order == 1 else "改为线性", {"order": 2 if order == 1 else 1}),
        ("改为均匀核" if kernel == "triangular" else "改为三角核",
         {"kernel": "uniform" if kernel == "triangular" else "triangular"}),
        (f"随访要求{followup + 30}天", {"followup": min(365, followup + 30)}),
    ]
    for name, changes in definitions:
        merged = {**base, **changes}
        try:
            result = estimate(data, merged)
            late = next((item for item in result["estimates"] if item["id"] == "late_voucher"), None)
            itt = next((item for item in result["estimates"] if item["id"] == "itt_eligibility"), None)
            first = next((item for item in result["estimates"] if item["id"] == "first_stage_voucher"), None)
            variants.append({
                "name": name,
                "selection": {key: merged.get(key) for key in ("window", "donut", "followup", "order", "kernel")},
                "status": result["status"],
                "status_label": result["status_label"],
                "n": result["summary"]["included_n"],
                "effective_n": result["support"]["effective_n"],
                "late": late["estimate"] if late else None,
                "late_interval": late["interval"] if late else None,
                "itt": itt["estimate"] if itt else None,
                "first_stage": first["estimate"] if first else None,
            })
        except ValueError as error:
            variants.append({"name": name, "selection": merged, "status": "invalid",
                             "status_label": "参数无效：" + str(error)})
    return variants
