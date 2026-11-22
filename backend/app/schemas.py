"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


# ── 模型评估验收 ──
class AcceptanceCreatePayload(BaseModel):
    required_samples: list[str] = Field(default_factory=list)
    group_id: str | int | None = None
    group_name: str | None = None
    metric_version: str | None = None
    metric_snapshot: Any = None
    policy: str = "early_close"
    scores: Any = None
    sources: Any = None
    algorithm: str = "xgboost"
    auto_submit: bool = False


class AcceptanceSubmitPayload(BaseModel):
    scores: Any = None
    sources: Any = None
    note: str | None = None


class AcceptanceClosePayload(BaseModel):
    reason: str | None = None


class AcceptanceRankPayload(BaseModel):
    scores: Any = None
    eval_ids: list[int] | None = None
    threshold: float = 0.7
