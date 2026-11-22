"""模型注册中心领域服务：训练完成的模型归档、版本管理与评估指标存储。"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from app.services.base import BaseModuleService
from app.services.score_validation import classify_score, display_raw
from app.store import store


class ModelRegistryService(BaseModuleService):
    MODULE = "model_registry"
    ENTITY = "模型注册"
    OPERATION_TERM = "存储"
    LIST_FIELDS = ["模型编号", "模型名称", "算法类型", "训练特征", "模型版本", "模型文件", "评估分数", "部署状态"]
    REQUIRED_FIELDS = ["模型编号", "模型名称", "算法类型"]
    STATUS_ORDER = ["草稿", "待验收", "已注册", "已废弃"]
    ACTION_RULES = {"提交验收": "待验收", "确认注册": "已注册", "标记废弃": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ── 链路4: 模型注册 → 批量预测 → 挖掘结果 ──
    def deploy_and_predict(self, model_id: int) -> dict[str, Any]:
        """将注册完成的模型部署并创建批量预测任务，对全量数据切片执行推理。"""
        model = store.find(self.MODULE, model_id)
        if model is None:
            return {"ok": False, "message": f"模型 {model_id} 不存在"}
        conclusion = str(model.get("验收结论") or "")
        if model.get("status") == "已废弃":
            return {
                "ok": False,
                "code": "model_deprecated",
                "status": model.get("status"),
                "message": f"模型 {model_id} 已废弃，不能部署预测",
            }
        if conclusion == "不通过":
            return {
                "ok": False,
                "code": "acceptance_failed",
                "status": model.get("status"),
                "message": (
                    f"模型 {model_id} 的验收结论为不通过，禁止注册与部署；"
                    "如需重新评估请新开验收"
                ),
            }
        if model.get("验收编号") and model.get("status") != "已注册":
            return {
                "ok": False,
                "code": "acceptance_pending",
                "status": model.get("status"),
                "message": (
                    f"模型 {model_id} 的验收尚未通过（{conclusion}），"
                    "局部排行或待补证状态都不能触发注册与部署"
                ),
            }
        model["部署状态"] = "已部署"
        model["status"] = "已注册"
        model["pending"] = False
        predict_rows = store.rows("predict_batch")
        next_id = max((int(r.get("id", 0)) for r in predict_rows), default=0) + 1
        predict_rows.append({
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "任务编号": f"PRED-{next_id:04d}",
            "模型编号": str(model_id),
            "数据切片": "full_dataset_slice_001",
            "输出格式": "csv",
            "分片策略": "round_robin",
            "并行度": "16",
            "预测状态": "草稿",
            "结果校验": "auto_validate",
        })
        return {"ok": True, "model_id": model_id, "predict_task_id": next_id, "score": model.get("评估分数")}

    def collect_predict_results_to_mining(self, model_id: int) -> dict[str, Any]:
        """采集批量预测结果并导入挖掘结果模块，生成洞察摘要和关键分群。"""
        model = store.find(self.MODULE, model_id)
        if model is None:
            return {"ok": False}
        mining_rows = store.rows("mining_result")
        next_id = max((int(r.get("id", 0)) for r in mining_rows), default=0) + 1
        mining_rows.append({
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "结果编号": f"RES-{next_id:04d}",
            "分析主题": f"模型{model_id}预测结果挖掘分析",
            "算法类型": model.get("算法类型", "unknown"),
            "洞察摘要": f"基于模型{model_id}的批量预测发现了3个高价值用户分群，置信度0.85",
            "关键分群": "高价值_群1,活跃_群2,流失风险_群3",
            "置信度": "0.85",
            "结果文件": f"mining/model_{model_id}_results.json",
            "结果版本": "v1.0.0",
        })
        return {"ok": True, "model_id": model_id, "mining_id": next_id}

    def validate_model_quality(self, model_id: int) -> dict[str, Any]:
        """校验单条模型的评估分数与特征完整性。

        这里的分数校验和 :mod:`score_validation` 保持一致：空值 / 空字符串
        是待补分，NaN / 无穷 / 非数值是无效，二者都不能按 0 分放行。
        """
        model = store.find(self.MODULE, model_id)
        if model is None:
            return {"ok": False, "code": "model_not_found"}

        check = classify_score(model.get("评估分数"))
        if not check["valid"]:
            # 待补分 / 无效都不能按 0 分放行；只标记异常，不擅自改已注册状态。
            if model.get("status") in {"草稿", "待验收"}:
                model["status"] = "待验收"
                model["pending"] = True
                model["abnormal"] = True
            return {
                "ok": False,
                "code": f"score_{check['code']}",
                "status": check["status"],
                "reason": check["reason"],
                "raw": display_raw(check["raw"]),
                "score": None,
                "qualified": False,
                "model_id": model_id,
            }

        score = check["value"]
        is_qualified = score >= 0.7
        if is_qualified:
            model["status"] = "已注册"
            model["pending"] = False
            model["abnormal"] = False
        elif model.get("status") in {"草稿", "待验收"}:
            model["status"] = "待验收"
            model["pending"] = True
            model["abnormal"] = True
        return {
            "ok": is_qualified,
            "score": score,
            "qualified": is_qualified,
            "model_id": model_id,
            "code": "ok" if is_qualified else "below_threshold",
            "reason": "" if is_qualified else f"评分 {score} 低于门槛 0.7",
        }

    def archive_deprecated_models(self, model_ids: list[int]) -> dict[str, Any]:
        """将废弃模型归档并清理存储空间，保留元数据供后续查询。"""
        archived = []
        for mid in model_ids:
            model = store.find(self.MODULE, mid)
            if model is None:
                continue
            model["status"] = "已废弃"
            model["abnormal"] = False
            archived.append(mid)
        return {"ok": True, "archived_count": len(archived), "archived_ids": archived}

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """动作也要服从验收结论：不通过 / 待补证的模型不能手工注册。

        验收通过由 :class:`AcceptanceService` 自动落成「已注册」；这里只
        拦截人工绕过验收的「确认注册」，并保留废弃动作。
        """
        model = store.find(self.MODULE, entry_id)
        if model is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        if action == "确认注册":
            conclusion = str(model.get("验收结论") or "")
            if conclusion == "不通过":
                return None, (
                    f"{self.ENTITY} {entry_id} 的验收结论为不通过，禁止注册；"
                    "如需重新评估请新开验收"
                )
            if conclusion == "待补证":
                return None, (
                    f"{self.ENTITY} {entry_id} 的验收仍在待补证，补齐必评样本前不能注册"
                )
            if model.get("验收编号") and conclusion != "通过":
                return None, f"{self.ENTITY} {entry_id} 尚未完成组验收，不能注册"
        return super().run_action(entry_id, action)

    # ── 验收联动：有效评估数据 + 例外记录 + 注册控制 ──
    def record_acceptance(self, application: Mapping[str, Any]) -> int:
        """把一次组验收的结果登记到模型注册中心。

        - 记录本次验收实际拿到的评估来源、分数状态、指标版本与结论；
        - 只有结论为「通过」才注册模型，其余保持待验收并禁止下游部署；
        - 同组的例外记录（待补分 / 无效 / 低于门槛 / 补录被拒）一并关联。

        已结束的验收不会因为后来的数据改写而重新注册或改变结论。
        """
        acceptance_no = str(application.get("验收编号"))
        receipt = application.get("receipt") or {}
        conclusion = str(application.get("conclusion") or receipt.get("conclusion") or "待补证")
        accepted = conclusion == "通过"
        status = "已注册" if accepted else "待验收"

        rows = store.rows(self.MODULE)
        existing = next(
            (row for row in rows if str(row.get("验收编号")) == acceptance_no),
            None,
        )
        if existing is not None:
            # 已结束的验收：旧记录不再被后来的数据改写。
            if existing.get("验收结论") in {"通过", "不通过"}:
                return int(existing.get("id", 0))
            model_id = int(existing.get("id", 0))
            model = existing
        else:
            model_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
            model = {
                "id": model_id,
                "模型编号": f"MODEL-{model_id:04d}",
                "模型名称": f"{application.get('组名称') or 'group'}_accepted",
                "算法类型": application.get("algorithm", "xgboost"),
                "训练特征": application.get("组标识", ""),
                "模型版本": "v1.0.0",
                "模型文件": f"models/acceptance_{acceptance_no}.pkl",
                "评估分数": "",
                "部署状态": "未部署",
            }
            rows.append(model)

        scores = receipt.get("statuses") or {}
        valid_scores = [
            float(item["score"])
            for item in scores.values()
            if item.get("status") == "有效" and item.get("score") is not None
        ]
        sources = {
            sample: item.get("source")
            for sample, item in scores.items()
        }
        metric_version = str(
            application.get("metric_version") or application.get("指标版本") or ""
        )
        # 验收记录第一次落地时的指标版本，后续轮次不覆盖。
        original_metric_version = str(
            model.get("最初指标版本")
            or model.get("指标版本")
            or metric_version
        )

        model.update({
            "status": status,
            "pending": not accepted,
            "abnormal": not accepted,
            "评估分数": str(min(valid_scores)) if valid_scores else "",
            "验收编号": acceptance_no,
            "验收结论": conclusion,
            "验收状态": receipt.get("status") or application.get("status"),
            "验收策略": receipt.get("policy") or application.get("验收策略"),
            "指标版本": metric_version,
            "最初指标版本": original_metric_version,
            "评估来源": sources,
            "评估状态": {
                sample: {
                    "status": item.get("status"),
                    "reason": item.get("reason"),
                    "score": item.get("score"),
                    "locked": item.get("locked"),
                }
                for sample, item in scores.items()
            },
            "验收轮次": receipt.get("round", 0),
            "验收阈值": receipt.get("threshold", 0.7),
            "验收最小分": receipt.get("min_score"),
            "验收均分": receipt.get("mean_score"),
            "注册控制": "允许注册" if accepted else "禁止注册",
            "补录控制": "允许补录" if receipt.get("can_supplement") else "禁止补录",
            "例外记录": self._link_exceptions(acceptance_no),
            "部署状态": "未部署" if accepted else model.get("部署状态", "未部署"),
        })
        return model_id

    def _link_exceptions(self, acceptance_no: str) -> list[dict[str, Any]]:
        rows = store.rows("evaluation_exception")
        return [
            {
                "id": row.get("id"),
                "样本编号": row.get("样本编号"),
                "状态": row.get("状态"),
                "代码": row.get("代码"),
                "原因": row.get("原因"),
                "原始值": row.get("原始值"),
                "轮次": row.get("轮次"),
                "已消解": bool(row.get("已消解")),
                "消解轮次": row.get("消解轮次"),
            }
            for row in rows
            if str(row.get("验收编号")) == acceptance_no
        ]

    def get_acceptance_record(self, acceptance_no: str | int) -> dict[str, Any] | None:
        """查询某次验收在注册中心的落地记录（含最初指标版本）。"""
        for row in store.rows(self.MODULE):
            if str(row.get("验收编号")) == str(acceptance_no):
                return copy.deepcopy(row)
        return None
