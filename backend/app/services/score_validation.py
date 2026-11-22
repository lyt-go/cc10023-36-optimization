"""评估分数校验与排序。

特征重要性指标里经常混入空值、空字符串、NaN、无穷或非数值。旧实现直接
``float(...)``，一条坏值就会让整批排行抛异常。这里把校验和排序拆开：

- 有效数值（包含 0）正常参与排行；
- 空字符串 / 空值标为「待补分」；
- NaN / 无穷 / 非数值标为「无效」，并保留可读原因。

校验函数对任何输入都不抛异常，返回值统一带 ``status``、``valid``、
``code`` 和 ``reason``，方便上层直接把原因写进回执和例外记录。
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

SCORE_VALID = "有效"
SCORE_PENDING = "待补分"
SCORE_INVALID = "无效"

SCORE_KEYS = ("score", "value", "评估分数", "信息值IV", "metric_value")
SAMPLE_KEYS = ("sample_id", "样本编号", "eval_id", "评估编号", "id")


def display_raw(raw: Any) -> Any:
    """把 NaN / 无穷转成可读文本，避免回执序列化出非法 JSON。"""
    if isinstance(raw, float):
        if math.isnan(raw):
            return "NaN"
        if math.isinf(raw):
            return "Infinity" if raw > 0 else "-Infinity"
    if isinstance(raw, bool):
        return str(raw)
    return raw


def _pending(raw: Any, code: str, reason: str) -> dict[str, Any]:
    return {
        "raw": display_raw(raw),
        "value": None,
        "valid": False,
        "status": SCORE_PENDING,
        "code": code,
        "reason": reason,
    }


def _invalid(raw: Any, code: str, reason: str) -> dict[str, Any]:
    return {
        "raw": display_raw(raw),
        "value": None,
        "valid": False,
        "status": SCORE_INVALID,
        "code": code,
        "reason": reason,
    }


def _valid(raw: Any, number: float) -> dict[str, Any]:
    return {
        "raw": display_raw(raw),
        "value": number,
        "valid": True,
        "status": SCORE_VALID,
        "code": "ok",
        "reason": "",
    }


def classify_score(value: Any) -> dict[str, Any]:
    """校验单个评分，永不抛异常。

    ``status`` 取值：``有效`` / ``待补分`` / ``无效``。只有 ``有效`` 的分数
    才能参与排行、门槛判断和模型注册。
    """
    if value is None:
        return _pending(value, "missing", "暂未提供评分（空值）")

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return _pending(value, "empty_string", "评分为空字符串")
        lowered = text.casefold()
        if lowered in {"nan", "+nan", "-nan"}:
            return _invalid(value, "nan", "评分是 NaN，不是有效数值")
        if lowered in {
            "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity",
        }:
            return _invalid(value, "infinity", "评分是无穷值，不是有效数值")
        try:
            number = float(text)
        except (TypeError, ValueError):
            return _invalid(value, "non_numeric", f"评分不是数值：{text!r}")
        if math.isnan(number):
            return _invalid(value, "nan", "评分是 NaN，不是有效数值")
        if math.isinf(number):
            return _invalid(value, "infinity", "评分是无穷值，不是有效数值")
        return _valid(value, number)

    if isinstance(value, bool):
        return _invalid(value, "non_numeric", "评分是布尔值，不是数值")

    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return _invalid(value, "nan", "评分是 NaN，不是有效数值")
        if math.isinf(number):
            return _invalid(value, "infinity", "评分是无穷值，不是有效数值")
        return _valid(value, number)

    return _invalid(value, "non_numeric", f"评分不是数值：{value!r}")


def sample_id_of(item: Mapping[str, Any], fallback: int | None = None) -> str:
    """从一条记录里取样本编号；取不到时用序号兜底。"""
    for key in SAMPLE_KEYS:
        if key in item and item.get(key) is not None:
            return str(item.get(key))
    return str(fallback) if fallback is not None else ""


def score_value_of(item: Mapping[str, Any]) -> Any:
    """从一条记录里取原始评分；缺失时返回 None（待补分）。"""
    for key in SCORE_KEYS:
        if key in item:
            return item.get(key)
    return None


def _as_mapping(item: Any, index: int) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    if isinstance(item, (tuple, list)) and len(item) >= 2:
        return {"sample_id": item[0], "score": item[1]}
    # 纯值列表：用下标当样本编号，值就是评分本身。
    return {"sample_id": str(index), "score": item}


def rank_items(
    items: Sequence[Any],
    *,
    threshold: float | None = None,
) -> dict[str, Any]:
    """对任意评分记录做分批排行，坏值不会让整批失败。

    返回结构：

    - ``ranked``：有效分数降序排行（0 正常入榜）；
    - ``pending``：空字符串 / 空值，带原因；
    - ``invalid``：NaN / 无穷 / 非数值，带原因。

    每一条只在自己的记录里失败，不影响其他记录。
    """
    ranked: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []

    if isinstance(items, Mapping):
        items = [{"sample_id": key, "score": value} for key, value in items.items()]
        source_items: list[Any] = list(items)
    else:
        source_items = list(items or [])

    for index, raw_item in enumerate(source_items):
        try:
            item = _as_mapping(raw_item, index)
            sample_id = sample_id_of(item, index)
            check = classify_score(score_value_of(item))
            record = {
                "sample_id": sample_id,
                "status": check["status"],
                "score": check["value"],
                "raw": check["raw"],
                "code": check["code"],
                "reason": check["reason"],
                "source": item.get("source"),
                "name": item.get("name") or item.get("feature") or item.get("关联特征"),
                "index": index,
            }
            for key, value in item.items():
                if key not in record and key not in SCORE_KEYS:
                    record[key] = value
            if check["valid"]:
                if threshold is not None:
                    record["meets_threshold"] = check["value"] >= threshold
                ranked.append(record)
            elif check["status"] == SCORE_PENDING:
                pending.append(record)
            else:
                invalid.append(record)
        except Exception as exc:  # 单条异常也只影响自己，不拖垮整批
            invalid.append({
                "sample_id": str(index),
                "status": SCORE_INVALID,
                "score": None,
                "raw": None,
                "code": "unexpected_error",
                "reason": f"评分解析异常：{exc}",
                "source": None,
                "name": None,
                "index": index,
            })

    ranked.sort(key=lambda row: (-float(row["score"]), row["index"]))
    for position, row in enumerate(ranked, start=1):
        row["rank"] = position

    return {
        "ok": True,
        "ranked": ranked,
        "pending": pending,
        "invalid": invalid,
        "excluded": pending + invalid,
        "total": len(source_items),
        "valid_count": len(ranked),
        "pending_count": len(pending),
        "invalid_count": len(invalid),
    }


def rank_values(values: Sequence[Any]) -> dict[str, Any]:
    """按纯值列表排行，序号即样本编号。"""
    return rank_items([{"sample_id": str(i), "score": value} for i, value in enumerate(values)])


def evaluate_group(
    scores: Sequence[Any],
    *,
    threshold: float = 0.7,
) -> dict[str, Any]:
    """按组判断能否通过。

    规则是「每一份都达标」，不是均值达标：任何一份待补分、无效或低于门槛，
    整组都不通过。均值只作为参考值返回，不参与判定。
    """
    checks: list[dict[str, Any]] = []
    for index, raw in enumerate(scores or []):
        item = raw if isinstance(raw, Mapping) else {"sample_id": str(index), "score": raw}
        check = classify_score(score_value_of(item))
        check["sample_id"] = sample_id_of(item, index)
        check["meets_threshold"] = bool(check["valid"] and check["value"] >= threshold)
        checks.append(check)

    valid = [check for check in checks if check["valid"]]
    pending = [check for check in checks if check["status"] == SCORE_PENDING]
    invalid = [check for check in checks if check["status"] == SCORE_INVALID]
    below = [check for check in valid if check["value"] < threshold]

    values = [check["value"] for check in valid]
    mean = round(sum(values) / len(values), 6) if values else None
    minimum = min(values) if values else None

    reasons: list[str] = []
    if not checks:
        reasons.append("必评样本清单为空")
    if pending:
        reasons.append("存在待补分样本：" + "、".join(c["sample_id"] for c in pending))
    if invalid:
        reasons.append("存在无效评分样本：" + "、".join(c["sample_id"] for c in invalid))
    if below:
        reasons.append(
            "以下样本低于门槛 "
            + f"{threshold}："
            + "、".join(f"{c['sample_id']}={c['value']}" for c in below)
        )

    qualified = bool(checks) and not pending and not invalid and not below
    return {
        "ok": qualified,
        "qualified": qualified,
        "threshold": threshold,
        "checks": checks,
        "valid_count": len(valid),
        "pending_count": len(pending),
        "invalid_count": len(invalid),
        "below_threshold": [c["sample_id"] for c in below],
        "min_score": minimum,
        "mean_score": mean,
        "reasons": reasons,
        "rule": "每一份必评样本都必须有效且不低于门槛，均值不作为放行依据",
    }
