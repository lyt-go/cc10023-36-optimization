"""模型评估验收接口：提交评分、补录证据、提前结案与查询回执。

除了 ``/api/acceptance`` 这套验收资源，还把这组能力挂到两个业务模块下：

- ``/api/feature_iv/scores/*``：评分校验与排行；
- ``/api/model_registry/acceptance/*``：组验收申请与回执。

同一份处理函数挂多个前缀，避免业务逻辑在路由层复制。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    AcceptanceClosePayload,
    AcceptanceCreatePayload,
    AcceptanceRankPayload,
    AcceptanceSubmitPayload,
)
from app.services.acceptance import acceptance_service
from app.services.feature_iv import FeatureIvService
from app.services.model_registry import ModelRegistryService
from app.services.score_validation import rank_items

router = APIRouter(prefix="/api/acceptance", tags=["模型评估验收"])
feature_iv_router = APIRouter(prefix="/api/feature_iv", tags=["特征重要性评分校验"])
registry_router = APIRouter(prefix="/api/model_registry", tags=["模型注册验收"])


# ── 处理函数 ──
def _list_applications(
    status: str | None,
    group_id: str | None,
    page: int,
    size: int,
) -> dict[str, Any]:
    items, total = acceptance_service.list_applications(
        status=status, group_id=group_id, page=page, size=size)
    return {"items": items, "total": total, "page": page, "size": size}


def _create_application(payload: AcceptanceCreatePayload) -> dict[str, Any]:
    try:
        return acceptance_service.create_application(
            payload.required_samples,
            group_id=payload.group_id,
            group_name=payload.group_name,
            metric_version=payload.metric_version,
            metric_snapshot=payload.metric_snapshot,
            policy=payload.policy,
            scores=payload.scores,
            sources=payload.sources,
            algorithm=payload.algorithm,
            auto_submit=payload.auto_submit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _get_application(acceptance_id: int) -> dict[str, Any]:
    application = acceptance_service.get_application(acceptance_id)
    if application is None:
        raise HTTPException(status_code=404, detail=f"验收申请 {acceptance_id} 不存在")
    return application


def _submit(acceptance_id: int, payload: AcceptanceSubmitPayload) -> dict[str, Any]:
    return acceptance_service.submit(
        acceptance_id,
        payload.scores,
        sources=payload.sources,
        note=payload.note,
    )


def _close(acceptance_id: int, payload: AcceptanceClosePayload) -> dict[str, Any]:
    return acceptance_service.close_application(acceptance_id, reason=payload.reason)


def _receipt(acceptance_id: int) -> dict[str, Any]:
    receipt = acceptance_service.get_receipt(acceptance_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"验收申请 {acceptance_id} 暂无回执")
    return receipt


def _exceptions(acceptance_id: int) -> dict[str, Any]:
    return {"items": acceptance_service.all_exceptions(acceptance_id)}


def _registry_record(acceptance_no: str) -> dict[str, Any]:
    record = ModelRegistryService().get_acceptance_record(acceptance_no)
    if record is None:
        raise HTTPException(status_code=404, detail=f"验收 {acceptance_no} 尚未在注册中心落地")
    return record


def _rank(payload: AcceptanceRankPayload) -> dict[str, Any]:
    if payload.eval_ids:
        return FeatureIvService().rank_features_by_iv_report(payload.eval_ids)
    if payload.scores is None:
        raise HTTPException(status_code=400, detail="必须提供 scores 或 eval_ids")
    return rank_items(payload.scores, threshold=payload.threshold)


def _rank_eval_ids(eval_ids: str) -> dict[str, Any]:
    try:
        parsed = [int(part) for part in eval_ids.split(",") if part.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="eval_ids 必须是逗号分隔的整数") from exc
    return FeatureIvService().rank_features_by_iv_report(parsed)


# ── /api/acceptance ──
@router.get("/applications")
def list_applications(
    status: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return _list_applications(status, group_id, page, size)


@router.post("/applications")
def create_application(payload: AcceptanceCreatePayload) -> dict[str, Any]:
    return _create_application(payload)


@router.get("/applications/{acceptance_id}")
def get_application(acceptance_id: int) -> dict[str, Any]:
    return _get_application(acceptance_id)


@router.post("/applications/{acceptance_id}/submit")
def submit_scores(acceptance_id: int, payload: AcceptanceSubmitPayload) -> dict[str, Any]:
    return _submit(acceptance_id, payload)


@router.post("/applications/{acceptance_id}/close")
def close_application(acceptance_id: int, payload: AcceptanceClosePayload) -> dict[str, Any]:
    return _close(acceptance_id, payload)


@router.get("/applications/{acceptance_id}/receipt")
def get_receipt(acceptance_id: int) -> dict[str, Any]:
    return _receipt(acceptance_id)


@router.get("/applications/{acceptance_id}/exceptions")
def get_exceptions(acceptance_id: int) -> dict[str, Any]:
    return _exceptions(acceptance_id)


@router.get("/registry/{acceptance_no}")
def get_registry_record(acceptance_no: str) -> dict[str, Any]:
    return _registry_record(acceptance_no)


@router.post("/rank")
def rank_scores(payload: AcceptanceRankPayload) -> dict[str, Any]:
    return _rank(payload)


# ── /api/feature_iv/... ──
@feature_iv_router.post("/scores/validate")
def validate_scores(payload: AcceptanceRankPayload) -> dict[str, Any]:
    return _rank(payload)


@feature_iv_router.post("/scores/rank")
def rank_scores_alias(payload: AcceptanceRankPayload) -> dict[str, Any]:
    return _rank(payload)


@feature_iv_router.get("/rank")
def rank_by_eval_ids(eval_ids: str = Query(default="")) -> dict[str, Any]:
    return _rank_eval_ids(eval_ids)


# ── /api/model_registry/acceptance... ──
@registry_router.get("/acceptance")
def list_registry_applications(
    status: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    return _list_applications(status, group_id, page, size)


@registry_router.post("/acceptance")
def create_registry_application(payload: AcceptanceCreatePayload) -> dict[str, Any]:
    return _create_application(payload)


@registry_router.get("/acceptance/{acceptance_id}")
def get_registry_application(acceptance_id: int) -> dict[str, Any]:
    return _get_application(acceptance_id)


@registry_router.post("/acceptance/{acceptance_id}/submit")
def submit_registry_scores(
    acceptance_id: int, payload: AcceptanceSubmitPayload
) -> dict[str, Any]:
    return _submit(acceptance_id, payload)


@registry_router.post("/acceptance/{acceptance_id}/close")
def close_registry_application(
    acceptance_id: int, payload: AcceptanceClosePayload
) -> dict[str, Any]:
    return _close(acceptance_id, payload)


@registry_router.get("/acceptance/{acceptance_id}/receipt")
def get_registry_receipt(acceptance_id: int) -> dict[str, Any]:
    return _receipt(acceptance_id)


@registry_router.get("/acceptance/{acceptance_id}/exceptions")
def get_registry_exceptions(acceptance_id: int) -> dict[str, Any]:
    return _exceptions(acceptance_id)


@registry_router.get("/acceptance-record/{acceptance_no}")
def get_acceptance_record(acceptance_no: str) -> dict[str, Any]:
    return _registry_record(acceptance_no)
