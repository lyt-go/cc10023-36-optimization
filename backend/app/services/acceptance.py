"""组验收工作流：把有效模型评估数据交给模型注册中心。

与「单条分数够门槛就注册」不同，这里以**登记的必评样本清单**为准：

- 每一份必评样本都必须拿到有效分数，且不低于 0.7，整组才算通过；
- 均值只做参考，不参与放行；
- 本次验收里已确认有效的分数会被锁定，后续补录不能覆盖；
- ``待补证`` 的申请可以继续补交缺失证据；
- ``通过`` / ``不通过`` 的申请已经结束，旧回执不会被后来的数据改写，需要新开验收。

提前结案与等待完整证据的冲突通过 ``policy`` 表达：

- ``early_close``（默认）：一旦某份**已确认有效**的必评样本低于门槛，立刻结案为
  ``不通过``，不再等剩下的缺失证据；
- ``wait_complete``：即使已经确定无法通过，也保留 ``待补证``，等证据齐备后再因此
  结束为 ``不通过``。

无论采用哪种策略，局部排行都不会触发模型注册；只有整组验收通过才会注册。
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.store import store
from app.services.score_validation import (
    SCORE_INVALID,
    SCORE_PENDING,
    SCORE_VALID,
    classify_score,
    display_raw,
    sample_id_of,
    score_value_of,
)

ACCEPTANCE_TABLE = "evaluation_acceptance"
CHECKLIST_TABLE = "evaluation_checklist"
EXCEPTION_TABLE = "evaluation_exception"

THRESHOLD = 0.7

POLICY_EARLY_CLOSE = "early_close"
POLICY_WAIT_COMPLETE = "wait_complete"
POLICIES = {
    POLICY_EARLY_CLOSE: "提前结案",
    POLICY_WAIT_COMPLETE: "等待完整证据",
}

STATUS_PENDING = "待补证"
STATUS_PASSED = "已通过"
STATUS_FAILED = "不通过"

CONCLUSION_PENDING = "待补证"
CONCLUSION_PASS = "通过"
CONCLUSION_FAIL = "不通过"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_id(table: str) -> int:
    rows = store.rows(table)
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _normalize_scores(scores: Any) -> dict[str, Any]:
    """把 dict / list / 纯值列表统一成 {样本编号: 原始评分}。"""
    if scores is None:
        return {}
    if isinstance(scores, Mapping):
        return {str(key): value for key, value in scores.items()}
    if isinstance(scores, (list, tuple)):
        normalized: dict[str, Any] = {}
        for index, item in enumerate(scores):
            if isinstance(item, Mapping):
                normalized[sample_id_of(item, index)] = score_value_of(item)
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                normalized[str(item[0])] = item[1]
            else:
                normalized[str(index)] = item
        return normalized
    raise TypeError("scores 必须是对象或数组")


def _normalize_sources(sources: Any, samples: Sequence[str]) -> dict[str, str]:
    if sources is None:
        return {sample: "acceptance" for sample in samples}
    if isinstance(sources, str):
        return {sample: sources for sample in samples}
    if isinstance(sources, Mapping):
        return {sample: str(sources.get(sample) or "acceptance") for sample in samples}
    return {sample: "acceptance" for sample in samples}


def _record_from_check(check: dict[str, Any], *, source: str | None, round_no: int) -> dict[str, Any]:
    return {
        "raw": check["raw"],
        "value": check["value"],
        "status": check["status"],
        "code": check["code"],
        "reason": check["reason"],
        "source": source,
        "locked": check["valid"],
        "evidence_round": round_no,
    }


def _missing_record() -> dict[str, Any]:
    return {
        "raw": None,
        "value": None,
        "status": SCORE_PENDING,
        "code": "missing_required",
        "reason": "必评样本尚未提交评分",
        "source": None,
        "locked": False,
        "evidence_round": None,
    }


def _exception_row(
    acceptance: Mapping[str, Any],
    record: Mapping[str, Any],
    sample: str,
    round_no: int,
    extra_reason: str = "",
) -> dict[str, Any]:
    return {
        "id": _next_id(EXCEPTION_TABLE),
        "验收编号": acceptance["验收编号"],
        "组标识": acceptance.get("组标识"),
        "样本编号": sample,
        "状态": record["status"],
        "代码": record["code"],
        "原因": record["reason"] if not extra_reason else (
            extra_reason if not record["reason"] else f"{record['reason']}（{extra_reason}）"
        ),
        "原始值": display_raw(record["raw"]),
        "评分": record["value"],
        "来源": record.get("source"),
        "轮次": round_no,
        "创建时间": _now(),
    }


class AcceptanceService:
    """验收申请的状态机与回执生成。"""

    # ── 创建 ──
    def create_application(
        self,
        required_samples: Sequence[str] | None = None,
        *,
        group_id: str | int | None = None,
        group_name: str | None = None,
        metric_version: str | None = None,
        metric_snapshot: Any = None,
        policy: str = POLICY_EARLY_CLOSE,
        scores: Any = None,
        sources: Any = None,
        algorithm: str = "xgboost",
        auto_submit: bool = False,
    ) -> dict[str, Any]:
        required: list[str] = []
        for sample in required_samples or []:
            text = str(sample).strip()
            if text and text not in required:
                required.append(text)
        if not required:
            raise ValueError("必须登记必评样本清单，清单不能为空")
        if policy not in POLICIES:
            raise ValueError(f"未知验收策略：{policy}，可选 {sorted(POLICIES)}")

        now = _now()
        acceptance_id = _next_id(ACCEPTANCE_TABLE)
        number = f"ACC-{acceptance_id:04d}"
        version = str(metric_version or self._default_metric_version())

        application = {
            "id": acceptance_id,
            "验收编号": number,
            "组标识": str(group_id) if group_id is not None else number,
            "组名称": group_name or f"特征组{number}",
            "必评样本清单": list(required),
            "验收策略": policy,
            "验收策略说明": POLICIES[policy],
            "algorithm": algorithm,
            "status": STATUS_PENDING,
            "conclusion": CONCLUSION_PENDING,
            "metric_version": version,
            "指标版本": version,
            "metric_snapshot": copy.deepcopy(metric_snapshot) if metric_snapshot is not None else [],
            "scores": {},
            "extra_scores": {},
            "warnings": [],
            "rounds": [],
            "receipt": None,
            "exception_ids": [],
            "registry_id": None,
            "registration_allowed": False,
            "supplementation_allowed": True,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "threshold": THRESHOLD,
        }
        store.rows(ACCEPTANCE_TABLE).append(application)

        for sample in required:
            store.rows(CHECKLIST_TABLE).append({
                "id": _next_id(CHECKLIST_TABLE),
                "验收编号": number,
                "组标识": application["组标识"],
                "样本编号": sample,
                "必评": True,
                "指标版本": version,
                "登记时间": now,
            })

        if auto_submit or scores is not None:
            result = self.submit(acceptance_id, scores or {}, sources=sources)
            application_view = result.get("application") or self.get_application(acceptance_id)
            application_view = dict(application_view or {})
            application_view["acceptance_id"] = acceptance_id
            application_view["ok"] = True
            return application_view

        # 即使还没提交评分，也先让注册中心拿到一条待验收关联记录。
        self._sync_registry(application)
        view = self.get_application(acceptance_id) or {}
        view["acceptance_id"] = acceptance_id
        view["ok"] = True
        return view

    def _default_metric_version(self) -> str:
        versions = [
            str(row.get("指标版本"))
            for row in store.rows("feature_iv")
            if row.get("指标版本")
        ]
        if versions:
            return sorted(versions)[-1]
        return "iv-v1"

    # ── 查询 ──
    def list_applications(
        self,
        *,
        status: str | None = None,
        group_id: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(ACCEPTANCE_TABLE))
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if group_id is not None:
            rows = [row for row in rows if str(row.get("组标识")) == str(group_id)]
        total = len(rows)
        start = (page - 1) * size
        return copy.deepcopy(rows[start:start + size]), total

    def get_application(self, acceptance_id: int) -> dict[str, Any] | None:
        row = store.find(ACCEPTANCE_TABLE, acceptance_id)
        return copy.deepcopy(row) if row else None

    def _find(self, acceptance_id: int) -> dict[str, Any] | None:
        return store.find(ACCEPTANCE_TABLE, acceptance_id)

    def get_receipt(self, acceptance_id: int) -> dict[str, Any] | None:
        row = self._find(acceptance_id)
        return copy.deepcopy(row.get("receipt")) if row else None

    # ── 提交 / 补录 ──
    def submit(
        self,
        acceptance_id: int,
        scores: Any,
        *,
        sources: Any = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        application = self._find(acceptance_id)
        if application is None:
            return {
                "ok": False,
                "code": "acceptance_not_found",
                "message": f"验收申请 {acceptance_id} 不存在",
                "status": None,
                "receipt": None,
            }

        required = application["必评样本清单"]

        # 允许直接按必评清单顺序传纯值数组，例如 [0, "", NaN]。
        if (
            isinstance(scores, (list, tuple))
            and scores
            and all(not isinstance(item, (Mapping, list, tuple)) for item in scores)
            and len(scores) == len(required)
        ):
            submitted = {sample: raw for sample, raw in zip(required, scores)}
        else:
            submitted = _normalize_scores(scores)

        sample_sources = _normalize_sources(sources, list(submitted))

        # 已结束的申请：拒绝续做，且不触碰旧回执。
        if application["status"] != STATUS_PENDING:
            old_receipt = application.get("receipt")
            return {
                "ok": False,
                "code": "acceptance_closed",
                "status": application["status"],
                "conclusion": application["conclusion"],
                "message": (
                    f"验收 {application['验收编号']} 已结束（{application['status']}），"
                    "本次提交被拒绝，旧回执保持不变；如需重新评估请新开验收。"
                ),
                "needs_new_acceptance": True,
                "can_supplement": False,
                "can_register": False,
                "receipt": copy.deepcopy(old_receipt),
            }

        round_no = len(application["rounds"]) + 1
        warnings: list[str] = []
        received: dict[str, Any] = {}
        rejected_overwrites: list[dict[str, Any]] = []

        records: dict[str, dict[str, Any]] = application["scores"]
        for sample in required:
            records.setdefault(sample, _missing_record())

        for sample, raw in submitted.items():
            check = classify_score(raw)
            source = sample_sources.get(sample, "acceptance")
            entry = {
                "raw": check["raw"],
                "value": check["value"],
                "status": check["status"],
                "code": check["code"],
                "reason": check["reason"],
                "source": source,
            }
            received[sample] = entry

            if sample not in required:
                application["extra_scores"][sample] = entry | {"evidence_round": round_no}
                warnings.append(
                    f"样本 {sample} 不在登记的必评清单内，仅记录、不参与本轮验收判定"
                )
                continue

            existing = records[sample]
            if existing.get("locked"):
                # 已确认有效：本次验收内不可被补录覆盖。
                if check["valid"] and check["value"] == existing["value"]:
                    # 复述同一个有效分数不算覆盖，静默保留即可。
                    continue
                rejected_overwrites.append({
                    "sample_id": sample,
                    "kept": existing["value"],
                    "ignored": display_raw(raw),
                    "reason": "该评分已在本轮验收中确认有效，补录不可覆盖",
                })
                warnings.append(
                    f"样本 {sample} 的有效评分已锁定，本次补录值 "
                    f"{display_raw(raw)!r} 被忽略，仍采用 {existing['value']}"
                )
                continue

            records[sample] = _record_from_check(check, source=source, round_no=round_no)

        # 缺席的必评样本保持待补分。
        for sample in required:
            if sample not in records or records[sample].get("status") is None:
                records[sample] = _missing_record()

        evaluation = self._evaluate(application, round_no)
        exception_rows = self._append_exceptions(application, evaluation, round_no)
        exception_rows += self._append_override_exceptions(
            application, rejected_overwrites, round_no)

        receipt = self._build_receipt(
            application,
            evaluation,
            round_no=round_no,
            received=received,
            warnings=warnings,
            rejected_overwrites=rejected_overwrites,
            exception_rows=exception_rows,
            note=note,
        )

        application["warnings"] = warnings
        application["rounds"].append(receipt)
        application["receipt"] = copy.deepcopy(receipt)
        application["status"] = receipt["status"]
        application["conclusion"] = receipt["conclusion"]
        application["updated_at"] = receipt["submitted_at"]
        application["registration_allowed"] = receipt["can_register"]
        application["supplementation_allowed"] = receipt["can_supplement"]
        if application["status"] != STATUS_PENDING:
            application["closed_at"] = receipt["submitted_at"]

        self._sync_registry(application)

        return {
            "ok": True,
            "code": "accepted",
            "status": application["status"],
            "conclusion": application["conclusion"],
            "message": receipt["message"],
            "needs_new_acceptance": receipt["needs_new_acceptance"],
            "can_supplement": receipt["can_supplement"],
            "can_register": receipt["can_register"],
            "receipt": copy.deepcopy(receipt),
            "application": copy.deepcopy(application),
        }

    # ── 主动提前结案 ──
    def close_application(self, acceptance_id: int, *, reason: str | None = None) -> dict[str, Any]:
        application = self._find(acceptance_id)
        if application is None:
            return {"ok": False, "code": "acceptance_not_found", "message": "验收申请不存在"}
        if application["status"] != STATUS_PENDING:
            return {
                "ok": False,
                "code": "acceptance_closed",
                "status": application["status"],
                "message": "验收已经结束，不能重复结案；如需重新评估请新开验收。",
                "receipt": copy.deepcopy(application.get("receipt")),
            }

        round_no = len(application["rounds"]) + 1
        records = application["scores"]
        for sample in application["必评样本清单"]:
            records.setdefault(sample, _missing_record())

        evaluation = self._evaluate(application, round_no)
        evaluation["conclusion"] = CONCLUSION_FAIL
        evaluation["status"] = STATUS_FAILED
        evaluation["qualified"] = False
        evaluation["certain_fail"] = True
        evaluation["reasons"] = list(evaluation.get("reasons") or [])
        evaluation["reasons"].append(reason or "复核员选择提前结案，不再等待剩余证据")

        exception_rows = self._append_exceptions(application, evaluation, round_no)
        receipt = self._build_receipt(
            application,
            evaluation,
            round_no=round_no,
            received={},
            warnings=["本轮回执由提前结案生成"],
            rejected_overwrites=[],
            exception_rows=exception_rows,
            note=reason,
            forced=True,
        )
        application["rounds"].append(receipt)
        application["receipt"] = copy.deepcopy(receipt)
        application["status"] = STATUS_FAILED
        application["conclusion"] = CONCLUSION_FAIL
        application["updated_at"] = receipt["submitted_at"]
        application["closed_at"] = receipt["submitted_at"]
        application["registration_allowed"] = False
        application["supplementation_allowed"] = False
        self._sync_registry(application)
        return {
            "ok": True,
            "code": "closed",
            "status": application["status"],
            "conclusion": application["conclusion"],
            "message": receipt["message"],
            "receipt": copy.deepcopy(receipt),
            "application": copy.deepcopy(application),
        }

    # ── 判定 ──
    def _evaluate(self, application: Mapping[str, Any], round_no: int) -> dict[str, Any]:
        required = application["必评样本清单"]
        records = application["scores"]
        threshold = float(application.get("threshold", THRESHOLD))

        checks: list[dict[str, Any]] = []
        for index, sample in enumerate(required):
            record = records.get(sample) or _missing_record()
            check = dict(record)
            check["score"] = record.get("value")
            check["sample_id"] = sample
            check["index"] = index
            check["meets_threshold"] = bool(
                record["status"] == SCORE_VALID and float(record["value"]) >= threshold
            )
            checks.append(check)

        ranked = [c for c in checks if c["status"] == SCORE_VALID]
        ranked.sort(key=lambda c: (-float(c["value"]), c["index"]))
        for position, row in enumerate(ranked, start=1):
            row["rank"] = position
        pending = [c for c in checks if c["status"] == SCORE_PENDING]
        invalid = [c for c in checks if c["status"] == SCORE_INVALID]
        below = [c for c in ranked if float(c["value"]) < threshold]

        all_valid = len(ranked) == len(required)
        certain_fail = bool(below)
        policy = application.get("验收策略", POLICY_EARLY_CLOSE)

        if all_valid and not below:
            status = STATUS_PASSED
            conclusion = CONCLUSION_PASS
            reasons: list[str] = []
        elif certain_fail:
            if policy == POLICY_EARLY_CLOSE:
                status = STATUS_FAILED
                conclusion = CONCLUSION_FAIL
            elif pending or invalid:
                # 已知无法通过，但按策略继续等证据齐备。
                status = STATUS_PENDING
                conclusion = CONCLUSION_PENDING
            else:
                status = STATUS_FAILED
                conclusion = CONCLUSION_FAIL
            reasons = [
                "以下已确认有效的必评样本低于门槛 "
                + f"{threshold}："
                + "、".join(f"{c['sample_id']}={c['value']}" for c in below)
            ]
        elif pending or invalid:
            status = STATUS_PENDING
            conclusion = CONCLUSION_PENDING
            reasons = []
        else:
            status = STATUS_FAILED
            conclusion = CONCLUSION_FAIL
            reasons = ["没有可用的有效评分，无法通过验收"]

        if pending:
            reasons.append("存在待补分样本：" + "、".join(c["sample_id"] for c in pending))
        if invalid:
            reasons.append("存在无效评分样本：" + "、".join(c["sample_id"] for c in invalid))

        valid_values = [float(c["value"]) for c in ranked]
        return {
            "checks": checks,
            "ranked": ranked,
            "pending": pending,
            "invalid": invalid,
            "below": below,
            "all_valid": all_valid,
            "certain_fail": certain_fail,
            "status": status,
            "conclusion": conclusion,
            "reasons": reasons,
            "min_score": min(valid_values) if valid_values else None,
            "mean_score": round(sum(valid_values) / len(valid_values), 6) if valid_values else None,
            "threshold": threshold,
            "policy": policy,
        }

    # ── 例外记录 ──
    def _append_exceptions(
        self,
        application: Mapping[str, Any],
        evaluation: Mapping[str, Any],
        round_no: int,
    ) -> list[dict[str, Any]]:
        rows = store.rows(EXCEPTION_TABLE)
        created: list[dict[str, Any]] = []
        for check in evaluation["checks"]:
            if check["status"] == SCORE_VALID and check["meets_threshold"]:
                # 本轮已修复先前的问题：把同组未解决的例外标成已消解，
                # 避免后续轮次/注册中心看起来仍有悬而未决的异常。
                self._resolve_previous(application, check["sample_id"], round_no)
                continue
            reason = "" if check["status"] != SCORE_VALID else "已确认有效但低于门槛"
            # 有效但低于门槛的记录，代码从 ok 改成 below_threshold，便于检索。
            code = "below_threshold" if check["status"] == SCORE_VALID and not check["meets_threshold"] else check["code"]
            signature = (
                check["status"], code, display_raw(check["raw"]), check["value"],
            )
            previous = next(
                (
                    row for row in reversed(rows)
                    if str(row.get("验收编号")) == application["验收编号"]
                    and str(row.get("样本编号")) == str(check["sample_id"])
                    and (
                        row.get("状态"), row.get("代码"),
                        row.get("原始值"), row.get("评分"),
                    ) == signature
                ),
                None,
            )
            if previous is not None:
                # 同一问题重复出现时不制造重复例外，沿用已有记录。
                if previous["id"] not in application["exception_ids"]:
                    application["exception_ids"].append(previous["id"])
                created.append(previous)
                continue
            row = _exception_row(
                application, {**check, "code": code}, check["sample_id"], round_no, reason)
            rows.append(row)
            created.append(row)
            application["exception_ids"].append(row["id"])
        return created

    def _resolve_previous(
        self,
        application: Mapping[str, Any],
        sample_id: str,
        round_no: int,
    ) -> None:
        """把同组同一样本先前未解决的例外标记为已消解。"""
        for row in store.rows(EXCEPTION_TABLE):
            if (
                str(row.get("验收编号")) == application["验收编号"]
                and str(row.get("样本编号")) == str(sample_id)
                and not row.get("已消解")
            ):
                row["已消解"] = True
                row["消解轮次"] = round_no

    def _append_override_exceptions(
        self,
        application: Mapping[str, Any],
        rejected: Sequence[Mapping[str, Any]],
        round_no: int,
    ) -> list[dict[str, Any]]:
        rows = store.rows(EXCEPTION_TABLE)
        created: list[dict[str, Any]] = []
        for item in rejected:
            row = {
                "id": _next_id(EXCEPTION_TABLE),
                "验收编号": application["验收编号"],
                "组标识": application.get("组标识"),
                "样本编号": item["sample_id"],
                "状态": SCORE_VALID,
                "代码": "locked_score_override",
                "原因": item["reason"],
                "原始值": item["ignored"],
                "评分": item["kept"],
                "来源": "supplement",
                "轮次": round_no,
                "创建时间": _now(),
            }
            rows.append(row)
            created.append(row)
            application["exception_ids"].append(row["id"])
        return created

    # ── 回执 ──
    def _build_receipt(
        self,
        application: Mapping[str, Any],
        evaluation: Mapping[str, Any],
        *,
        round_no: int,
        received: Mapping[str, Any],
        warnings: Sequence[str],
        rejected_overwrites: Sequence[Mapping[str, Any]],
        exception_rows: Sequence[Mapping[str, Any]],
        note: str | None,
        forced: bool = False,
    ) -> dict[str, Any]:
        status = evaluation["status"]
        pending = evaluation["pending"]
        invalid = evaluation["invalid"]
        below = evaluation["below"]
        missing_ids = [c["sample_id"] for c in pending if c["code"] == "missing_required"]
        problem_ids = [c["sample_id"] for c in pending + invalid]
        certain_fail = evaluation.get("certain_fail", False)
        policy = application.get("验收策略", POLICY_EARLY_CLOSE)

        can_supplement = status == STATUS_PENDING
        can_register = evaluation["conclusion"] == CONCLUSION_PASS
        needs_new_acceptance = not can_supplement

        if status == STATUS_PASSED:
            message = (
                "验收通过：登记的必评样本全部有效且不低于门槛 "
                f"{evaluation['threshold']}，有效评估数据已交给模型注册中心注册。"
            )
        elif status == STATUS_FAILED:
            detail = "；".join(evaluation["reasons"]) or "未满足通过条件"
            message = (
                f"验收结束为不通过：{detail}。本回执已定稿，后续数据不能改写；"
                "如需重新评估请新开验收。"
            )
        elif certain_fail:
            done = "、".join(f"{c['sample_id']}={c['value']}" for c in below)
            waiting = "、".join(problem_ids) or "剩余证据"
            message = (
                f"已确认必评样本 {done} 低于门槛，本轮最终结论不通过已经确定；"
                f"按「{POLICIES.get(policy, policy)}」策略仍保留待补证，可继续补交 "
                f"{waiting} 以完善证据，补齐后本轮结束为不通过。"
            )
        else:
            waiting = "、".join(problem_ids) or "缺失证据"
            message = (
                f"仍可补交缺失证据：{waiting}。"
                "每份必评样本都有效且不低于门槛后才会通过；已确认有效的评分不可被补录覆盖。"
            )
            if missing_ids:
                message += f" 缺少必评样本：{'、'.join(missing_ids)}。"
        if forced:
            message = (
                "验收已按复核员要求提前结案为不通过，不再等待剩余证据；"
                "本回执已定稿，后续数据不能改写，如需重新评估请新开验收。"
            )
        if note:
            message += f" 备注：{note}"

        sources = {
            check["sample_id"]: check.get("source")
            for check in evaluation["checks"]
        }
        statuses = {
            check["sample_id"]: {
                "status": check["status"],
                "score": check["value"],
                "reason": check["reason"],
                "source": check.get("source"),
                "locked": bool(check.get("locked")),
            }
            for check in evaluation["checks"]
        }

        return {
            "round": round_no,
            "submitted_at": _now(),
            "status": status,
            "conclusion": evaluation["conclusion"],
            "policy": policy,
            "policy_label": POLICIES.get(policy, policy),
            "threshold": evaluation["threshold"],
            "metric_version": application["metric_version"],
            "required_samples": list(application["必评样本清单"]),
            "received": copy.deepcopy(dict(received)),
            "sources": sources,
            "statuses": statuses,
            "checks": copy.deepcopy(evaluation["checks"]),
            "ranked": copy.deepcopy(evaluation["ranked"]),
            "pending": copy.deepcopy(pending),
            "invalid": copy.deepcopy(invalid),
            "below_threshold": [c["sample_id"] for c in below],
            "min_score": evaluation["min_score"],
            "mean_score": evaluation["mean_score"],
            "all_valid": evaluation["all_valid"],
            "certain_fail": certain_fail,
            "can_supplement": can_supplement,
            "can_register": can_register,
            "needs_new_acceptance": needs_new_acceptance,
            "supplementation": "允许补录" if can_supplement else "不可补录",
            "registration": "允许注册" if can_register else "禁止注册",
            "warnings": list(warnings),
            "rejected_overwrites": copy.deepcopy(list(rejected_overwrites)),
            "exception_ids": [row["id"] for row in exception_rows],
            "reasons": list(evaluation["reasons"]),
            "message": message,
        }

    # ── 交给模型注册中心 ──
    def _sync_registry(self, application: Mapping[str, Any]) -> None:
        from app.services.model_registry import ModelRegistryService

        registry_id = ModelRegistryService().record_acceptance(application)
        application["registry_id"] = registry_id

    def all_exceptions(self, acceptance_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(EXCEPTION_TABLE)
        if acceptance_id is None:
            return copy.deepcopy(list(rows))
        application = self._find(acceptance_id)
        if application is None:
            return []
        number = application["验收编号"]
        return copy.deepcopy([row for row in rows if row.get("验收编号") == number])


def reset_acceptance_state() -> None:
    """清空验收运行期数据（测试用）。"""
    for table in (ACCEPTANCE_TABLE, CHECKLIST_TABLE, EXCEPTION_TABLE):
        store.rows(table).clear()
    registry_rows = store.rows("model_registry")
    registry_rows[:] = [row for row in registry_rows if not row.get("验收编号")]


acceptance_service = AcceptanceService()
