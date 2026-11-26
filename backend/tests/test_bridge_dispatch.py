"""桥接分派测试：跨模块派工、资格判定、改派冲突、并发仲裁、幂等重试。

覆盖题述场景：
- F90 门店组派给甲、F91 渠道组派给乙，两个页签都有桥接编号 07 时各自独立派工；
- 甲的处置在途未写入时 F90/07 改派给乙，并发提交处分只能确认一项；
- 逐条显示两批归属与拒绝依据，其他桥接项不受影响继续派工；
- 丢掉回包后重开装配，重试只接续原操作，回执可分辨 F90/07 最终由谁完成。
"""
from __future__ import annotations

import threading

import pytest

from app.pipeline.bridge_dispatcher import (
    BridgeDispatcher,
    BridgeEntry,
    DispatchError,
    TODO_CONFIRMED,
    TODO_IN_FLIGHT,
    TODO_SUPERSEDED,
)


def _entry(batch: str, no: str, group: str, **kwargs) -> BridgeEntry:
    return BridgeEntry(
        assembly_batch=batch,
        bridge_no=no,
        fact_table=f"{batch}_事实表",
        dim_group=group,
        **kwargs,
    )


def _dispatcher() -> BridgeDispatcher:
    d = BridgeDispatcher()
    d.register_assembler("甲", ["门店组"])
    d.register_assembler("乙", ["门店组", "渠道组"])
    d.register_bridge(_entry("F90", "BRG-07", "门店组"))
    d.register_bridge(_entry("F90", "BRG-05", "门店组"))
    d.register_bridge(_entry("F90", "BRG-08", "门店组",
                             status="归一失败", normalization="失败"))
    d.register_bridge(_entry("F91", "BRG-07", "渠道组"))
    d.register_bridge(_entry("F91", "BRG-11", "渠道组", status="已作废"))
    return d


class TestEligibility:
    def test_group_qualification_per_assembler(self):
        d = _dispatcher()
        f90 = d.eligibility_of("F90", "BRG-07")
        rows = {r["装配员"]: r for r in f90["资格明细"]}
        assert rows["甲"]["有资格"] == "是"
        assert rows["乙"]["有资格"] == "是"

        f91 = d.eligibility_of("F91", "BRG-07")
        rows91 = {r["装配员"]: r for r in f91["资格明细"]}
        assert rows91["甲"]["有资格"] == "否"
        assert "渠道组" in rows91["甲"]["拒绝依据"]
        assert rows91["乙"]["有资格"] == "是"

    def test_abnormal_entry_rejects_everyone_with_reason(self):
        d = _dispatcher()
        rows = {r["装配员"]: r for r in d.eligibility_of("F90", "BRG-08")["资格明细"]}
        assert all(r["有资格"] == "否" for r in rows.values())
        assert "归一" in rows["甲"]["拒绝依据"]
        rows_void = {r["装配员"]: r for r in d.eligibility_of("F91", "BRG-11")["资格明细"]}
        assert "作废" in rows_void["乙"]["拒绝依据"]

    def test_unknown_entry_raises(self):
        d = _dispatcher()
        with pytest.raises(DispatchError):
            d.eligibility_of("F90", "BRG-404")

    def test_entry_view_shows_cross_batch_ownership(self):
        d = _dispatcher()
        view = d.entry_view("F90", "BRG-07")
        peers = view["同编号跨批归属"]
        assert len(peers) == 1
        assert peers[0]["装配批次"] == "F91"
        assert peers[0]["维度组"] == "渠道组"


class TestBatchDispatch:
    def test_multi_select_same_batch_dispatches_all(self):
        d = _dispatcher()
        result = d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"},
             {"assembly_batch": "F90", "bridge_no": "BRG-05"}],
            "甲", "F90", request_id="REQ-1",
        )
        assert result["success_count"] == 2
        assert result["reject_count"] == 0
        todos = d.todos("F90")
        assert len(todos) == 2
        assert all(t["指派装配员"] == "甲" and t["待办状态"] == TODO_IN_FLIGHT
                   for t in todos)

    def test_unqualified_assembler_rejected_item_by_item(self):
        d = _dispatcher()
        result = d.dispatch_batch(
            [{"assembly_batch": "F91", "bridge_no": "BRG-07"}],
            "甲", "F91", request_id="REQ-2",
        )
        assert result["success_count"] == 0
        assert "渠道组" in result["items"][0]["拒绝依据"]
        assert d.todos("F91") == []

    def test_other_items_continue_when_one_rejected(self):
        d = _dispatcher()
        result = d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-08"},
             {"assembly_batch": "F91", "bridge_no": "BRG-11"},
             {"assembly_batch": "F90", "bridge_no": "BRG-05"}],
            "乙", "F90", request_id="REQ-3",
        )
        assert result["success_count"] == 1
        assert result["reject_count"] == 2
        assert d.todos("F90")[0]["桥接编号"] == "BRG-05"

    def test_same_bridge_no_two_tabs_go_to_own_batches(self):
        """两个页签里都出现 BRG-07：F90 给甲、F91 给乙，互不串单。"""
        d = _dispatcher()
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "甲", "F90", request_id="REQ-F90",
        )
        d.dispatch_batch(
            [{"assembly_batch": "F91", "bridge_no": "BRG-07"}],
            "乙", "F91", request_id="REQ-F91",
        )
        f90 = d.entry_view("F90", "BRG-07")
        f91 = d.entry_view("F91", "BRG-07")
        assert f90["当前指派人"] == "甲"
        assert f91["当前指派人"] == "乙"
        assert f90["当前版本"] == 1 and f91["当前版本"] == 1


class TestReassignAndConcurrentDisposition:
    def test_reassign_supersedes_in_flight_todo_and_bumps_version(self):
        d = _dispatcher()
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "甲", "F90", request_id="REQ-A",
        )
        result = d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "乙", "F90", request_id="REQ-B",
        )
        assert result["items"][0]["版本前"] == 1
        assert result["items"][0]["版本后"] == 2
        statuses = [t["待办状态"] for t in d.todos("F90")]
        assert TODO_SUPERSEDED in statuses and TODO_IN_FLIGHT in statuses

    def test_stale_assignee_disposition_rejected_after_reassign(self):
        d = _dispatcher()
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "甲", "F90", request_id="REQ-A",
        )
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "乙", "F90", request_id="REQ-B",
        )
        # 甲拿着改派前的版本 1 提交，被版本/受让人规则拦截
        stale = d.submit_disposition_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07", "version": 1}],
            "甲", "F90", request_id="REQ-C1",
        )
        assert stale["items"][0]["是否成功"] == "否"
        assert stale["items"][0]["拒绝依据"]

    def test_concurrent_dispositions_only_one_confirmed(self):
        """两人并发提交处分：无论线程顺序如何，同一条目只确认一项。"""
        for order in ("jia-first", "yi-first"):
            d = _dispatcher()
            d.dispatch_batch(
                [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
                "甲", "F90", request_id=f"REQ-{order}-A",
            )
            d.dispatch_batch(
                [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
                "乙", "F90", request_id=f"REQ-{order}-B",
            )
            barrier = threading.Barrier(2)
            outcomes: dict[str, dict] = {}

            def submit(who: str, version: int, req: str) -> None:
                barrier.wait()
                outcomes[who] = d.submit_disposition_batch(
                    [{"assembly_batch": "F90", "bridge_no": "BRG-07",
                      "version": version}],
                    who, "F90", request_id=req,
                )

            t_jia = threading.Thread(
                target=submit, args=("甲", 1, f"REQ-{order}-C1"))
            t_yi = threading.Thread(
                target=submit, args=("乙", 2, f"REQ-{order}-C2"))
            if order == "jia-first":
                t_jia.start(); t_yi.start()
            else:
                t_yi.start(); t_jia.start()
            t_jia.join(); t_yi.join()

            flags = {who: out["items"][0]["是否成功"]
                     for who, out in outcomes.items()}
            assert list(flags.values()).count("是") == 1
            confirmed = [t for t in d.todos("F90") if t["待办状态"] == TODO_CONFIRMED]
            assert len(confirmed) == 1
            # 乙持有改派后的新版本，最终完成人只能是乙
            assert confirmed[0]["完成人"] == "乙"
            rejected = [out["items"][0] for out in outcomes.values()
                        if out["items"][0]["是否成功"] == "否"]
            assert rejected and rejected[0]["拒绝依据"]

    def test_in_flight_todo_blocked_when_entry_becomes_abnormal(self):
        d = _dispatcher()
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "甲", "F90", request_id="REQ-V1",
        )
        # 派工后条目被作废（模拟通用动作/外部状态变更）
        entry = d._entries[("F90", "BRG-07")]
        entry.status = "已作废"
        result = d.submit_disposition_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07", "version": 1}],
            "甲", "F90", request_id="REQ-V2",
        )
        assert result["items"][0]["是否成功"] == "否"
        assert "作废" in result["items"][0]["拒绝依据"]
        assert d.todos("F90")[0]["待办状态"] == TODO_IN_FLIGHT

    def test_other_bridge_items_unaffected_by_conflict(self):
        d = _dispatcher()
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"},
             {"assembly_batch": "F90", "bridge_no": "BRG-05"}],
            "甲", "F90", request_id="REQ-D",
        )
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "乙", "F90", request_id="REQ-E",
        )
        # BRG-05 没有改派，甲的在途处分照常确认
        ok = d.submit_disposition_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-05", "version": 1}],
            "甲", "F90", request_id="REQ-F",
        )
        assert ok["items"][0]["是否成功"] == "是"
        # F91/07 是另一条目，与 F90/07 的冲突完全隔离
        d.dispatch_batch(
            [{"assembly_batch": "F91", "bridge_no": "BRG-07"}],
            "乙", "F91", request_id="REQ-G",
        )
        ok91 = d.submit_disposition_batch(
            [{"assembly_batch": "F91", "bridge_no": "BRG-07", "version": 1}],
            "乙", "F91", request_id="REQ-H",
        )
        assert ok91["items"][0]["是否成功"] == "是"


class TestResumeAndReceipts:
    def test_resume_replays_original_request_idempotently(self):
        d = _dispatcher()
        first = d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"},
             {"assembly_batch": "F90", "bridge_no": "BRG-05"}],
            "甲", "F90", request_id="REQ-LOSS-1",
        )
        first_items = [dict(item, **{"是否接续重试": "是"}) for item in first["items"]]
        todo_count_before = len(d.todos())
        resumed = d.resume("REQ-LOSS-1")
        assert resumed["resumed"] is True
        assert resumed["items"] == first_items
        # 接续重试不产生新待办
        assert len(d.todos()) == todo_count_before
        assert all(item["是否接续重试"] == "是" for item in resumed["items"])

    def test_resume_unknown_request_raises(self):
        d = _dispatcher()
        with pytest.raises(DispatchError):
            d.resume("REQ-MISSING")

    def test_receipts_identify_who_completed_entry(self):
        d = _dispatcher()
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "甲", "F90", request_id="REQ-R1",
        )
        d.dispatch_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07"}],
            "乙", "F90", request_id="REQ-R2",
        )
        d.submit_disposition_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07", "version": 1}],
            "甲", "F90", request_id="REQ-R3",
        )
        d.submit_disposition_batch(
            [{"assembly_batch": "F90", "bridge_no": "BRG-07", "version": 2}],
            "乙", "F90", request_id="REQ-R4",
        )
        receipts = [r for r in d.receipts()
                    if r["装配批次"] == "F90" and r["桥接编号"] == "BRG-07"]
        confirmed = [r for r in receipts if r["操作类型"] == "disposition"
                     and r["是否成功"] == "是"]
        assert len(confirmed) == 1
        assert confirmed[0]["装配员"] == "乙"
        rejected = [r for r in receipts if r["是否成功"] == "否"]
        assert rejected
        assert all("版本" in r["拒绝依据"] or "受让人" in r["拒绝依据"]
                   for r in rejected)

    def test_overview_carries_two_batch_attribution_and_reject_reason(self):
        d = _dispatcher()
        d.dispatch_batch(
            [{"assembly_batch": "F91", "bridge_no": "BRG-07"}],
            "甲", "F91", request_id="REQ-O",
        )
        rows = {(r["装配批次"], r["桥接编号"]): r for r in d.dispatch_overview()}
        assert rows[("F90", "BRG-07")]["同编号批次归属"] == "F90、F91"
        assert rows[("F91", "BRG-07")]["最近拒绝依据"]
        assert "甲" not in rows[("F91", "BRG-07")]["有资格装配员"]
        assert "乙" in rows[("F91", "BRG-07")]["有资格装配员"]


class TestServiceLayer:
    """服务层：内存仓库桥接条目 → 分派器注册 → 派工结果回写。"""

    def _register_store_bridge(self, bridge_no: str, batch: str, group: str,
                               table: str) -> None:
        from app.store import store
        rows = store.rows("bridge_link")
        rows.append({
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "status": "待装配", "pending": True, "abnormal": False,
            "桥接编号": bridge_no, "所属事实表": table,
            "关联维度": group, "权重比例": "1.0", "桥接行数": 3,
            "归一校验": "通过", "桥接版本": "v1", "最近装配": "2026-11-26",
            "装配批次": batch, "维度组": group, "派工指派人": "",
        })
        from app.services.bridge_link import BridgeLinkService
        dispatcher = BridgeLinkService.get_dispatcher()
        dispatcher.register_bridge(BridgeEntry(
            assembly_batch=batch, bridge_no=bridge_no, fact_table=table,
            dim_group=group, status="待装配", normalization="通过",
        ))

    def test_seed_board_contains_both_f90_f91_bridge_07(self):
        from app.services.bridge_link import BridgeLinkService
        svc = BridgeLinkService()
        rows = {(r["装配批次"], r["桥接编号"]): r for r in svc.dispatch_overview()}
        assert ("F90", "BRG-07") in rows
        assert ("F91", "BRG-07") in rows
        assert rows[("F90", "BRG-07")]["维度组"] == "门店组"
        assert rows[("F91", "BRG-07")]["维度组"] == "渠道组"

    def test_dispatch_writes_back_assignee_to_store(self):
        from app.services.bridge_link import BridgeLinkService
        from app.store import store
        self._register_store_bridge("BRG-T01", "F90", "门店组", "门店销售事实表")
        svc = BridgeLinkService()
        svc.dispatch_entries(
            [{"assembly_batch": "F90", "bridge_no": "BRG-T01"}],
            "甲", "F90", request_id="REQ-SVC-1",
        )
        row = next(r for r in store.rows("bridge_link")
                   if r.get("桥接编号") == "BRG-T01" and r.get("装配批次") == "F90")
        assert row["派工指派人"] == "甲"

        svc.submit_dispositions(
            [{"assembly_batch": "F90", "bridge_no": "BRG-T01", "version": 1}],
            "甲", "F90", request_id="REQ-SVC-2",
        )
        assert row["status"] == "已装配"
        assert row["派工指派人"] == ""

    def test_assembly_todos_are_cross_module_visible(self):
        from app.services.bridge_link import BridgeLinkService
        self._register_store_bridge("BRG-T02", "F91", "渠道组", "渠道销售事实表")
        svc = BridgeLinkService()
        svc.dispatch_entries(
            [{"assembly_batch": "F91", "bridge_no": "BRG-T02"}],
            "乙", "F91", request_id="REQ-SVC-3",
        )
        todos = svc.assembly_todos("F91")
        assert any(t["桥接编号"] == "BRG-T02" and t["指派装配员"] == "乙"
                   for t in todos)

    def test_router_level_unknown_entry_returns_http_404(self):
        from fastapi import HTTPException
        from app.routers.bridge_dispatch import entry_view, resume_request
        from app.schemas import ResumePayload
        with pytest.raises(HTTPException) as exc:
            entry_view("F90", "BRG-404")
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            resume_request(ResumePayload(request_id="REQ-404"))
        assert exc.value.status_code == 404

    def test_create_entry_with_batch_auto_registers_to_board(self):
        from app.services.bridge_link import BridgeLinkService
        svc = BridgeLinkService()
        entry, missing = svc.create_entry({
            "桥接编号": "BRG-NEW", "所属事实表": "门店销售事实表",
            "关联维度": "门店维度", "装配批次": "F90", "维度组": "门店组",
        })
        assert missing == [] and entry is not None
        keys = {(r["装配批次"], r["桥接编号"]) for r in svc.dispatch_overview()}
        assert ("F90", "BRG-NEW") in keys
