"""组验收链路测试：坏值排序、补录、提前结案与注册控制。"""
from __future__ import annotations

import copy
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.acceptance import acceptance_service, reset_acceptance_state
from app.services.feature_iv import FeatureIvService
from app.services.model_registry import ModelRegistryService
from app.services.score_validation import classify_score
from app.store import store


def _registry_row(acceptance_no):
    return ModelRegistryService().get_acceptance_record(acceptance_no)


def _registry_count():
    return len(store.rows("model_registry"))


# ── 1. 坏值排序：0 入榜，空值待补分，NaN 无效并保留原因 ──
def test_rank_keeps_zero_and_separates_bad_values():
    reset_acceptance_state()
    before = _registry_count()
    report = FeatureIvService().rank_iv_scores(
        [
            {"sample_id": "A", "score": 0},
            {"sample_id": "B", "score": ""},
            {"sample_id": "C", "score": float("nan")},
        ]
    )
    assert report["ok"] is True
    assert [row["sample_id"] for row in report["ranked"]] == ["A"], report
    assert report["ranked"][0]["score"] == 0.0, "零分应正常入榜"

    pending = {row["sample_id"]: row for row in report["pending"]}
    invalid = {row["sample_id"]: row for row in report["invalid"]}
    assert set(pending) == {"B"} and pending["B"]["status"] == "待补分"
    assert pending["B"]["reason"], "待补分应保留原因"
    assert set(invalid) == {"C"} and invalid["C"]["status"] == "无效"
    assert invalid["C"]["code"] == "nan" and "NaN" in invalid["C"]["reason"]
    assert pending["B"]["reason"] != invalid["C"]["reason"], "B 和 C 的原因应不同"
    assert _registry_count() == before, "局部排行不能触发注册"


def test_rank_survives_infinity_and_non_numeric():
    report = FeatureIvService().rank_iv_scores(
        [{"sample_id": "inf", "score": float("inf")},
         {"sample_id": "text", "score": "not-a-number"},
         {"sample_id": "ok", "score": "0.82"}]
    )
    assert [row["sample_id"] for row in report["ranked"]] == ["ok"]
    codes = {row["sample_id"]: row["code"] for row in report["invalid"]}
    assert codes == {"inf": "infinity", "text": "non_numeric"}


def test_classify_score_zero_is_valid_and_blank_is_pending():
    assert classify_score(0)["status"] == "有效"
    assert classify_score("0.0")["status"] == "有效"
    assert classify_score(None)["status"] == "待补分"
    assert classify_score("")["status"] == "待补分"
    assert classify_score("NaN")["status"] == "无效"
    assert classify_score("-Infinity")["status"] == "无效"


# ── 2. 首轮混合坏值：默认提前结案，拒绝续做且旧回执不变 ──
def test_early_close_rejects_resubmission_and_keeps_receipt():
    reset_acceptance_state()
    app = acceptance_service.create_application(
        ["A", "B", "C"], group_id="GROUP-A", group_name="组A", metric_version="iv-2026-11-01")
    acceptance_no = app["验收编号"]
    assert app["status"] == "待补证"

    first = acceptance_service.submit(
        app["id"], {"A": 0, "B": "", "C": float("nan")}, sources={"A": "iv"})
    assert first["status"] == "不通过", first
    assert first["conclusion"] == "不通过"
    assert first["can_supplement"] is False
    assert first["can_register"] is False

    receipt = first["receipt"]
    assert [row["sample_id"] for row in receipt["ranked"]] == ["A"]
    assert receipt["ranked"][0]["score"] == 0.0
    assert [row["sample_id"] for row in receipt["pending"]] == ["B"]
    assert [row["sample_id"] for row in receipt["invalid"]] == ["C"]
    assert receipt["sources"]["A"] == "iv"
    assert receipt["statuses"]["B"]["status"] == "待补分"
    assert receipt["statuses"]["C"]["status"] == "无效"
    assert "新开验收" in receipt["message"]
    assert _registry_row(acceptance_no)["status"] == "待验收"

    frozen = copy.deepcopy(receipt)
    second = acceptance_service.submit(app["id"], {"B": 0.8, "C": 0.9})
    assert second["ok"] is False
    assert second["code"] == "acceptance_closed"
    assert second["needs_new_acceptance"] is True
    assert second["receipt"] == frozen, "旧回执必须保持原样"
    assert acceptance_service.get_receipt(app["id"]) == frozen

    stored = _registry_row(acceptance_no)
    assert stored["验收结论"] == "不通过"
    assert stored["status"] == "待验收"
    assert stored["部署状态"] == "未部署"


# ── 3. 等待完整证据：先待补证，补齐后仍因 A=0 结束 ──
def test_wait_complete_then_fail_after_evidence_complete():
    reset_acceptance_state()
    app = acceptance_service.create_application(
        ["A", "B", "C"], group_id="GROUP-B", policy="wait_complete")
    acceptance_no = app["验收编号"]

    first = acceptance_service.submit(app["id"], {"A": 0, "B": "", "C": float("nan")})
    assert first["status"] == "待补证", first
    assert first["can_supplement"] is True
    assert first["can_register"] is False
    assert "仍保留待补证" in first["message"]
    assert first["receipt"]["certain_fail"] is True

    second = acceptance_service.submit(app["id"], {"B": 0.8, "C": 0.9})
    assert second["status"] == "不通过", second
    assert second["can_supplement"] is False
    assert second["receipt"]["below_threshold"] == ["A"]
    assert "A" in second["message"]

    third = acceptance_service.submit(app["id"], {"A": 0.95})
    assert third["ok"] is False and third["code"] == "acceptance_closed"
    assert _registry_row(acceptance_no)["status"] == "待验收"

    # 新的验收才能重新评估
    reopened = acceptance_service.create_application(
        ["A", "B", "C"], group_id="GROUP-B", policy="wait_complete")
    assert reopened["验收编号"] != acceptance_no


# ── 4. 均值不能放行：0.60 / 0.90 / 0.90 ──
def test_average_does_not_pass_group():
    reset_acceptance_state()
    app = acceptance_service.create_application(
        ["S1", "S2", "S3"], group_id="GROUP-C", policy="wait_complete")
    result = acceptance_service.submit(app["id"], [0.60, 0.90, 0.90])
    receipt = result["receipt"]
    assert receipt["mean_score"] == 0.8 and receipt["min_score"] == 0.6
    assert result["status"] == "不通过"
    assert result["can_register"] is False
    assert receipt["below_threshold"] == ["S1"]
    assert _registry_row(app["验收编号"])["status"] == "待验收"


# ── 5. 全组达标才注册，并保留最初的指标版本 ──
def test_all_pass_registers_and_preserves_original_metric_version():
    reset_acceptance_state()
    earlier = acceptance_service.create_application(
        ["A", "B"], group_id="GROUP-D", metric_version="iv-2026-10-01",
        policy="wait_complete")
    acceptance_no = earlier["验收编号"]
    acceptance_service.submit(earlier["id"], {"A": 0, "B": 0.8})
    assert acceptance_service.get_application(earlier["id"])["status"] == "不通过"
    original = _registry_row(acceptance_no)
    assert original["指标版本"] == "iv-2026-10-01"
    assert original["验收结论"] == "不通过"

    good = acceptance_service.create_application(
        ["F1", "F2", "F3"], group_id="GROUP-E", metric_version="iv-2026-11-20")
    passed = acceptance_service.submit(
        good["id"], {"F1": 0.71, "F2": 0.88, "F3": 0.95},
        sources={"F1": "feature_iv", "F2": "feature_iv", "F3": "manual"})
    assert passed["status"] == "已通过", passed
    assert passed["can_register"] is True
    assert passed["receipt"]["message"].startswith("验收通过")

    record = _registry_row(good["验收编号"])
    assert record["status"] == "已注册"
    assert record["验收结论"] == "通过"
    assert record["评估分数"] == "0.71", "注册中心应记录组内最低有效分"
    assert record["最初指标版本"] == "iv-2026-11-20"
    assert record["注册控制"] == "允许注册"
    assert record["评估来源"] == {
        "F1": "feature_iv", "F2": "feature_iv", "F3": "manual"}
    assert all(item["status"] == "有效" for item in record["评估状态"].values())

    # 后续新的验收不会改写早先的验收记录，指标版本仍是当初那版。
    again = acceptance_service.create_application(
        ["F1", "F2", "F3"], group_id="GROUP-E", metric_version="iv-2026-12-01")
    acceptance_service.submit(again["id"], {"F1": 0.9, "F2": 0.9, "F3": 0.9})
    unchanged = _registry_row(acceptance_no)
    assert unchanged["指标版本"] == "iv-2026-10-01"
    assert unchanged["验收结论"] == "不通过"
    assert unchanged["评估状态"] == original["评估状态"]


# ── 6. 已确认有效的分数不可被补录覆盖 ──
def test_confirmed_valid_score_cannot_be_overwritten():
    reset_acceptance_state()
    app = acceptance_service.create_application(["A", "B"], group_id="GROUP-F")
    acceptance_service.submit(app["id"], {"A": 0.8})
    second = acceptance_service.submit(app["id"], {"A": 0.3, "B": 0.9})
    assert second["status"] == "已通过"
    scores = {row["sample_id"]: row for row in second["receipt"]["checks"]}
    assert scores["A"]["score"] == 0.8 and scores["A"]["locked"] is True
    assert second["receipt"]["rejected_overwrites"][0]["ignored"] == 0.3
    assert any("已锁定" in warning for warning in second["receipt"]["warnings"])

    record = _registry_row(app["验收编号"])
    assert record["评估分数"] == "0.8", "注册用的是锁定后的有效分"
    assert any(row["代码"] == "locked_score_override" for row in record["例外记录"])


# ── 7. 例外记录与模拟后端接口 ──
def test_exceptions_linked_to_registry_without_duplicates():
    reset_acceptance_state()
    app = acceptance_service.create_application(["A", "B"], group_id="GROUP-G")
    first = acceptance_service.submit(app["id"], {"A": 0, "B": ""})
    second = acceptance_service.submit(app["id"], {"B": 0.9})
    assert second["ok"] is False, "默认提前结案后不能续做"
    assert first["receipt"]["exception_ids"]

    record = _registry_row(app["验收编号"])
    exceptions = record["例外记录"]
    assert exceptions, record
    by_sample = {(row["样本编号"], row["状态"]) for row in exceptions}
    assert ("A", "有效") in by_sample
    assert ("B", "待补分") in by_sample
    ids = [row["id"] for row in exceptions]
    assert len(ids) == len(set(ids)), "例外记录不应重复"


def test_resolved_exception_marked_when_supplement_fixes_it():
    reset_acceptance_state()
    app = acceptance_service.create_application(
        ["A", "B"], group_id="GROUP-K", policy="wait_complete")
    acceptance_service.submit(app["id"], {"A": 0.9, "B": ""})
    first = _registry_row(app["验收编号"])
    unresolved = [row for row in first["例外记录"] if not row["已消解"]]
    assert [row["样本编号"] for row in unresolved] == ["B"]

    acceptance_service.submit(app["id"], {"B": 0.85})
    assert acceptance_service.get_application(app["id"])["status"] == "已通过"
    record = _registry_row(app["验收编号"])
    assert record["status"] == "已注册"
    assert all(row["已消解"] for row in record["例外记录"]), record["例外记录"]


def test_http_endpoints():
    """有 HTTP 测试依赖时验证接口连通；无依赖时跳过，不影响纯逻辑测试。"""
    try:
        from fastapi.testclient import TestClient
    except Exception:
        print("    (跳过 HTTP 用例：未安装 httpx2)")
        return
    from app.main import app as fastapi_app

    reset_acceptance_state()
    client = TestClient(fastapi_app)
    status = client.get("/api/health")
    assert status.status_code == 200

    created = client.post(
        "/api/model_registry/acceptance",
        json={"required_samples": ["A", "B", "C"], "group_id": "GROUP-H"},
    )
    assert created.status_code == 200, created.text
    acceptance_id = created.json()["id"]

    submitted = client.post(
        f"/api/feature_iv/scores/validate",
        json={"scores": {"A": 0, "B": "", "C": None}},
    )
    assert submitted.status_code == 200
    assert [row["sample_id"] for row in submitted.json()["ranked"]] == ["A"]

    receipt = client.get(f"/api/model_registry/acceptance/{acceptance_id}/receipt")
    assert receipt.status_code == 404


def test_registry_guards_block_manual_registration():
    reset_acceptance_state()
    registry = ModelRegistryService()
    app = acceptance_service.create_application(["A"], group_id="GROUP-I")
    acceptance_service.submit(app["id"], {"A": 0})
    model_id = registry.get_acceptance_record(app["验收编号"])["id"]

    entry, message = registry.run_action(model_id, "确认注册")
    assert entry is None and "不通过" in message, message
    deployed = registry.deploy_and_predict(model_id)
    assert deployed["ok"] is False and deployed["code"] == "acceptance_failed"

    pending = acceptance_service.create_application(
        ["A", "B"], group_id="GROUP-J", policy="wait_complete")
    acceptance_service.submit(pending["id"], {"A": 0.8})
    pending_model = registry.get_acceptance_record(pending["验收编号"])["id"]
    entry, message = registry.run_action(pending_model, "确认注册")
    assert entry is None and "待补证" in message, message
    assert registry.deploy_and_predict(pending_model)["code"] == "acceptance_pending"


def main() -> int:
    tests = [
        test_rank_keeps_zero_and_separates_bad_values,
        test_rank_survives_infinity_and_non_numeric,
        test_classify_score_zero_is_valid_and_blank_is_pending,
        test_early_close_rejects_resubmission_and_keeps_receipt,
        test_wait_complete_then_fail_after_evidence_complete,
        test_average_does_not_pass_group,
        test_all_pass_registers_and_preserves_original_metric_version,
        test_confirmed_valid_score_cannot_be_overwritten,
        test_exceptions_linked_to_registry_without_duplicates,
        test_resolved_exception_marked_when_supplement_fixes_it,
        test_registry_guards_block_manual_registration,
        test_http_endpoints,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {test.__name__}: {exc}")
            failed += 1
    print(f"\n总测试: {len(tests)}, 通过: {passed}, 失败: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
