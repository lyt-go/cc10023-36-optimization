"""特征重要性分析领域服务：维护特征预测能力、稳定性和区分度的评估管理。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.score_validation import (
    SCORE_VALID,
    classify_score,
    display_raw,
    rank_items,
    score_value_of,
)
from app.store import store


class FeatureIvService(BaseModuleService):
    MODULE = "feature_iv"
    ENTITY = "重要性评估"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["评估编号", "关联特征", "信息值IV", "特征覆盖率", "异常比例", "稳定性PSI", "单调性", "评估时间"]
    REQUIRED_FIELDS = ["评估编号", "关联特征", "信息值IV"]
    STATUS_ORDER = ["草稿", "计算中", "已完成", "已废弃"]
    ACTION_RULES = {"发起评估": "计算中", "确认完成": "已完成", "废弃结果": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ── 链路2: 特征筛选 → 特征重要性分析 ──
    def compute_iv_batch(
        self,
        eval_ids: list[int],
        metric_version: str = "iv-v1",
        scores: dict[int, Any] | None = None,
    ) -> dict[str, Any]:
        """批量计算特征IV值（信息值），对每个特征计算其预测能力并落库。

        ``scores`` 可以按评估编号直接指定本轮拿到的原始 IV（便于处理
        上游回传的坏值）。每条记录独立校验：有效值正常落库，空值 / 空
        字符串标记待补分，NaN / 无穷 / 非数值标记无效并保留原因，都不
        会让整批计算失败。
        """
        results = []
        for eid in eval_ids:
            entry = store.find(self.MODULE, eid)
            if entry is None:
                results.append({
                    "eval_id": eid,
                    "status": "无效",
                    "reason": "评估记录不存在",
                    "valid": False,
                })
                continue

            if scores is not None and eid in scores:
                raw_iv = scores[eid]
            else:
                raw_iv = round(0.1 + (eid % 10) * 0.04, 4)
            check = classify_score(raw_iv)

            entry["信息值IV"] = (
                str(check["value"]) if check["valid"] else display_raw(check["raw"])
            )
            entry["指标版本"] = metric_version
            entry["评分状态"] = check["status"]
            entry["评分原因"] = check["reason"]

            if check["valid"]:
                coverage = round(0.7 + (eid % 10) * 0.03, 4)
                psi = round(0.01 + (eid % 10) * 0.02, 4)
                entry["特征覆盖率"] = str(coverage)
                entry["稳定性PSI"] = str(psi)
                entry["异常比例"] = str(round(eid % 3 * 0.01, 4))
                entry["单调性"] = "positive" if eid % 2 == 0 else "non_monotonic"
                entry["status"] = "已完成"
                entry["pending"] = False
                entry["abnormal"] = False
                results.append({
                    "eval_id": eid,
                    "status": check["status"],
                    "valid": True,
                    "iv": check["value"],
                    "coverage": coverage,
                    "psi": psi,
                })
            else:
                # 坏值只影响自己：保留记录并标记状态，等复核补分。
                entry["status"] = "计算中" if check["status"] == "待补分" else "已废弃"
                entry["pending"] = True
                entry["abnormal"] = True
                results.append({
                    "eval_id": eid,
                    "status": check["status"],
                    "valid": False,
                    "code": check["code"],
                    "reason": check["reason"],
                    "raw": display_raw(check["raw"]),
                })

        valid_results = [row for row in results if row.get("valid")]
        return {
            "ok": len(valid_results) > 0,
            "results": results,
            "total_computed": len(valid_results),
            "total_rejected": len(results) - len(valid_results),
            "metric_version": metric_version,
        }

    # ── 评估分数校验（坏值不再拖垮整批） ──
    def validate_iv_scores(self, scores: list[Any] | dict[str, Any]) -> dict[str, Any]:
        """校验一批 IV / 评估分数，逐条给出可用性、状态和原因。

        有效数值（包含 0）可参与排序；空字符串与空值标为待补分；NaN、
        无穷和非数值标为无效并保留原因。任何一条坏值都不会让整批失败。
        """
        if isinstance(scores, dict):
            items = [{"sample_id": key, "score": value} for key, value in scores.items()]
        else:
            items = list(scores or [])
        return rank_items(items, threshold=0.7)

    def rank_iv_scores(self, scores: list[Any] | dict[str, Any]) -> dict[str, Any]:
        """对原始评分做排行，返回完整报告（有效/待补分/无效）。"""
        return self.validate_iv_scores(scores)

    def rank_features_by_iv(self, eval_ids: list[int] | None = None, *,
                            scores: list[Any] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """按IV值对特征进行降序排序，返回重要性排名列表供下游查询。

        单条记录的坏值只会被跳过，不会让整批排序失败。需要查看被跳过
        记录的待补分 / 无效原因时，使用 :meth:`rank_features_by_iv_report`。

        如果传入的编号在评估表里一条都找不到，则把入参当作原始评分处理，
        方便直接对 ``[0, "", NaN]`` 这类指标数组排错。
        """
        report = self.rank_features_by_iv_report(eval_ids or [], scores=scores)
        return [
            {
                "eval_id": row.get("eval_id"),
                "feature": row.get("feature"),
                "iv": row["score"],
                "psi": row.get("psi"),
                "rank": row.get("rank"),
                "status": row["status"],
                "source": row.get("source"),
            }
            for row in report["ranked"]
        ]

    def rank_features_by_iv_report(
        self,
        eval_ids: list[int] | None = None,
        *,
        scores: list[Any] | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """带原因的特征重要性排行报告。

        ``ranked`` 只包含可采用的评估分数；``pending`` 是空字符串 / 空值；
        ``invalid`` 是 NaN / 无穷 / 非数值，二者分别带原因。排序本身不会
        触发模型注册，局部结果也不会。
        """
        if scores is not None:
            if isinstance(scores, dict):
                raw_items = [{"sample_id": k, "score": v} for k, v in scores.items()]
            else:
                raw_items = list(scores)
            report = rank_items(raw_items, threshold=0.7)
            report["reasons"] = {
                row["sample_id"]: {"status": row["status"], "reason": row["reason"]}
                for row in report["pending"] + report["invalid"]
            }
            return report

        items: list[dict[str, Any]] = []
        found = 0
        for eid in eval_ids or []:
            entry = store.find(self.MODULE, eid)
            if entry is None:
                items.append({
                    "sample_id": str(eid),
                    "eval_id": eid,
                    "feature": None,
                    "score": None,
                    "source": "feature_iv",
                })
                continue
            found += 1
            iv_raw = entry.get("信息值IV")
            try:
                psi = float(str(entry.get("稳定性PSI", 0)))
            except (TypeError, ValueError):
                psi = None
            items.append({
                "sample_id": str(eid),
                "eval_id": eid,
                "feature": entry.get("关联特征"),
                "score": score_value_of({"信息值IV": iv_raw}),
                "psi": psi,
                "source": entry.get("指标版本") or entry.get("source") or "feature_iv",
                "metric_version": entry.get("指标版本"),
            })

        # 一条都没命中，且入参本身带非整数值时：把入参当成原始评分来校验，
        # 而不是评估编号。纯整数编号找不到则按缺失记录处理。
        all_int_ids = bool(eval_ids) and all(
            isinstance(item, int) and not isinstance(item, bool) for item in eval_ids
        )
        if items and found == 0 and not all_int_ids:
            report = rank_items(list(eval_ids or []), threshold=0.7)
            report["reasons"] = {
                row["sample_id"]: {"status": row["status"], "reason": row["reason"]}
                for row in report["pending"] + report["invalid"]
            }
            return report

        report = rank_items(items, threshold=0.7)
        by_id = {row["sample_id"]: row for row in report["ranked"]}
        for row in report["ranked"]:
            eval_id = row.get("eval_id")
            if eval_id is not None:
                row["eval_id"] = int(eval_id)
        for bucket in ("pending", "invalid", "excluded"):
            for row in report[bucket]:
                eval_id = row.get("eval_id")
                if eval_id is not None:
                    row["eval_id"] = int(eval_id)
        report["by_eval_id"] = by_id
        report["reasons"] = {
            row["sample_id"]: {"status": row["status"], "reason": row["reason"]}
            for row in report["pending"] + report["invalid"]
        }
        return report

    def validate_scores(self, scores: list[Any] | dict[str, Any]) -> dict[str, Any]:
        """``validate_iv_scores`` 的短别名。"""
        return self.validate_iv_scores(scores)

    def merge_iv_results_to_registry(self, eval_id: int) -> dict[str, Any]:
        """将单条特征重要性评估结果归并到模型注册中心。

        只接受有效且达到门槛的分数；待补分、无效或低于门槛一律拒绝，并
        返回原因。真正的组验收由 ``AcceptanceService`` 负责。
        """
        entry = store.find(self.MODULE, eval_id)
        if entry is None:
            return {"ok": False, "code": "iv_not_found", "message": f"评估 {eval_id} 不存在"}

        check = classify_score(entry.get("信息值IV"))
        if not check["valid"]:
            return {
                "ok": False,
                "code": f"score_{check['code']}",
                "status": check["status"],
                "reason": check["reason"],
                "raw": display_raw(check["raw"]),
                "message": f"评估 {eval_id} 的评分不可采用：{check['reason']}",
            }
        if check["value"] < 0.7:
            return {
                "ok": False,
                "code": "below_threshold",
                "status": SCORE_VALID,
                "score": check["value"],
                "reason": f"评分 {check['value']} 低于门槛 0.7",
                "message": f"评估 {eval_id} 未达到注册门槛，不能注册",
            }

        registry_rows = store.rows("model_registry")
        next_id = max((int(r.get("id", 0)) for r in registry_rows), default=0) + 1
        row = {
            "id": next_id,
            "status": "已注册",
            "pending": False,
            "abnormal": False,
            "模型编号": f"MODEL-{next_id:04d}",
            "模型名称": f"iv_model_{eval_id}",
            "算法类型": "lr",
            "训练特征": entry.get("关联特征", ""),
            "模型版本": "v1.0.0",
            "模型文件": f"models/iv_model_{eval_id}.pkl",
            "评估分数": str(check["value"]),
            "部署状态": "未部署",
            "指标版本": entry.get("指标版本") or "iv-v1",
            "评估来源": "feature_iv",
            "验收结论": "通过",
        }
        registry_rows.append(row)
        return {
            "ok": True,
            "model_id": next_id,
            "iv_value": check["value"],
            "metric_version": row["指标版本"],
            "status": "已注册",
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """确认完成前先校验评分，坏值不能被当成有效结果结案。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "确认完成":
            check = classify_score(entry.get("信息值IV"))
            if not check["valid"]:
                entry["评分状态"] = check["status"]
                entry["评分原因"] = check["reason"]
                entry["pending"] = True
                entry["abnormal"] = True
                return (
                    None,
                    f"{self.ENTITY} {entry_id} 的评分不可采用（{check['status']}）："
                    f"{check['reason']}，请补分后再确认完成",
                )
            entry["评分状态"] = check["status"]
            entry["评分原因"] = ""
        return super().run_action(entry_id, action)

    def calibrate_psi_drift(self, eval_id: int) -> dict[str, Any]:
        """基于PSI值校验特征稳定性，并将漂移指标关联到漂移监控模块。"""
        entry = store.find(self.MODULE, eval_id)
        if entry is None:
            return {"ok": False}
        try:
            psi = float(str(entry.get("稳定性PSI", 0)))
        except (TypeError, ValueError):
            return {"ok": False, "message": f"PSI 不是有效数值：{entry.get('稳定性PSI')!r}"}
        is_stable = psi < 0.25
        if not is_stable:
            drift_rows = store.rows("drift_monitor")
            next_id = max((int(r.get("id", 0)) for r in drift_rows), default=0) + 1
            drift_rows.append({
                "id": next_id, "status": "监控中", "pending": False, "abnormal": False,
                "监控编号": f"DRIFT-{next_id:04d}",
                "关联特征": entry.get("关联特征", ""),
                "基准分布": "baseline_v1",
                "当前分布": "current_v1",
                "PSI值": str(psi),
                "漂移阈值": "0.25",
                "采样窗口": "7d",
                "监控状态": "监控中",
            })
            return {"ok": True, "psi": psi, "alert": True, "drift_id": next_id}
        return {"ok": True, "psi": psi, "alert": False, "message": "特征稳定"}
