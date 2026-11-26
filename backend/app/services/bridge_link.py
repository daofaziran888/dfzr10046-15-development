"""多值桥接领域服务：事实与多值维度桥接的权重归一校验管理，产出桥接条目数据集。

新增桥接分派职责：桥接条目跨模块生成事实装配待办。
- 审核员按事实批次（F90 门店组 / F91 渠道组）多选条目批量派工；
- 逐条给出装配员的下一步处置资格与拒绝依据；
- 改派与在途请求冲突时用版本号仲裁，并发处分只确认一项；
- 回包丢失凭请求编号幂等重试，回执台账留痕可辨最终完成人。

分派状态机与并发仲裁在 app.pipeline.bridge_dispatcher.BridgeDispatcher 内，
本服务负责把内存仓库（store）中的桥接条目注册进分派器并同步派工结果。
"""
from __future__ import annotations

import threading
from typing import Any

from app.pipeline.bridge_dispatcher import BridgeDispatcher, BridgeEntry
from app.services.base import BaseModuleService
from app.store import store

# 装配员处置资格台账：甲只具备门店组资格，乙同时具备门店组与渠道组资格。
# 这样 F90/07（门店组）先派给甲、在途时可改派给乙；F91/07（渠道组）只能派给乙。
ASSEMBLER_GROUPS: dict[str, list[str]] = {
    "甲": ["门店组"],
    "乙": ["门店组", "渠道组"],
}


class BridgeLinkService(BaseModuleService):
    MODULE = "bridge_link"
    ENTITY = "桥接条目"
    LIST_FIELDS = [
        "桥接编号", "所属事实表", "关联维度", "权重比例", "桥接行数",
        "归一校验", "桥接版本", "最近装配", "装配批次", "维度组", "派工指派人",
    ]
    REQUIRED_FIELDS = ["桥接编号", "所属事实表", "关联维度"]
    STATUS_ORDER = ["待装配", "已装配", "归一失败", "已作废"]
    ACTION_RULES = {"启动装配": "已装配", "登记归一失败": "归一失败", "作废条目": "已作废"}
    NEGATIVE_ACTIONS = ["登记归一失败"]

    _dispatcher: BridgeDispatcher | None = None
    _dispatcher_lock = threading.Lock()

    # ------------------------------------------------------------ 分派器装配
    @classmethod
    def get_dispatcher(cls) -> BridgeDispatcher:
        """惰性初始化单例分派器：装配员资格 + 内存仓库中的桥接条目只装载一次。"""
        if cls._dispatcher is None:
            with cls._dispatcher_lock:
                if cls._dispatcher is None:
                    dispatcher = BridgeDispatcher()
                    for name, groups in ASSEMBLER_GROUPS.items():
                        dispatcher.register_assembler(name, groups)
                    for row in store.rows(cls.MODULE):
                        entry = cls._entry_from_row(row)
                        if entry is not None:
                            dispatcher.register_bridge(entry)
                    cls._dispatcher = dispatcher
        return cls._dispatcher

    @staticmethod
    def _entry_from_row(row: dict[str, Any]) -> BridgeEntry | None:
        """把仓库行转成分派器条目；未绑定事实批次的历史条目不进入派工台。"""
        batch = str(row.get("装配批次") or "").strip()
        bridge_no = str(row.get("桥接编号") or "").strip()
        if not batch or not bridge_no:
            return None
        return BridgeEntry(
            assembly_batch=batch,
            bridge_no=bridge_no,
            fact_table=str(row.get("所属事实表") or ""),
            dim_group=str(row.get("维度组") or ""),
            status=str(row.get("status") or "待装配"),
            normalization=str(row.get("归一校验") or "待校验"),
        )

    def _sync_dispatch_result(self, result: dict[str, Any]) -> None:
        """把派工/处分回执同步回仓库行，保证多值桥接列表与派工台状态一致。"""
        rows = store.rows(self.MODULE)
        for item in result.get("items", []):
            batch = item["装配批次"]
            bridge_no = item["桥接编号"]
            for row in rows:
                if row.get("装配批次") == batch and row.get("桥接编号") == bridge_no:
                    if item["操作类型"] == "dispatch" and item["是否成功"] == "是":
                        row["派工指派人"] = item["装配员"]
                    if item["操作类型"] == "disposition" and item["是否成功"] == "是":
                        row["派工指派人"] = ""
                        if item["说明"].startswith("处分已确认：装配完成"):
                            row["status"] = "已装配"
                            row["pending"] = False
                            row["归一校验"] = "通过"

    # ------------------------------------------------------------ 派工台视图
    def dispatch_overview(self, assembly_batch: str | None = None) -> list[dict[str, Any]]:
        """派工台列表：逐条展示两批归属、资格、在途待办与最近拒绝依据。"""
        return self.get_dispatcher().dispatch_overview(assembly_batch)

    def entry_dispatch_view(self, assembly_batch: str, bridge_no: str) -> dict[str, Any]:
        return self.get_dispatcher().entry_view(assembly_batch, bridge_no)

    def eligibility(self, assembly_batch: str, bridge_no: str) -> dict[str, Any]:
        return self.get_dispatcher().eligibility_of(assembly_batch, bridge_no)

    def assembly_todos(self, assembly_batch: str | None = None) -> list[dict[str, Any]]:
        """跨模块产物：事实装配待办。"""
        return self.get_dispatcher().todos(assembly_batch)

    def receipts(self, request_id: str | None = None) -> list[dict[str, Any]]:
        return self.get_dispatcher().receipts(request_id)

    # --------------------------------------------------------------- 动作入口
    def dispatch_entries(
        self,
        items: list[dict[str, str]],
        assembler: str,
        assembly_batch: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """批量派工/改派：同一批次多条条目一次分派，逐条返回回执。"""
        dispatcher = self.get_dispatcher()
        result = dispatcher.dispatch_batch(items, assembler, assembly_batch, request_id)
        self._sync_dispatch_result(result)
        return result

    def submit_dispositions(
        self,
        items: list[dict[str, Any]],
        assembler: str,
        assembly_batch: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """装配员提交处分：并发时由分派器版本仲裁，只确认一项。"""
        dispatcher = self.get_dispatcher()
        result = dispatcher.submit_disposition_batch(
            items, assembler, assembly_batch, request_id)
        self._sync_dispatch_result(result)
        return result

    def resume_request(self, request_id: str) -> dict[str, Any]:
        """回包丢失重试：接续原请求，回执标注「是否接续重试」。"""
        dispatcher = self.get_dispatcher()
        result = dispatcher.resume(request_id)
        self._sync_dispatch_result(result)
        return result

    # ------------------------------------------------- 通用登记/动作联动分派器
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记桥接条目；若已绑定事实批次，同步注册进派工台。"""
        entry, missing = super().create_entry(values)
        if entry is not None and str(entry.get("装配批次") or "").strip():
            dispatcher_entry = self._entry_from_row(entry)
            if dispatcher_entry is not None:
                self.get_dispatcher().register_bridge(dispatcher_entry)
        return entry, missing

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """通用状态动作后，把条目状态同步到派工台（作废/装配后不再可派工）。"""
        entry, message = super().run_action(entry_id, action)
        if entry is not None and str(entry.get("装配批次") or "").strip():
            dispatcher_entry = self._entry_from_row(entry)
            if dispatcher_entry is not None:
                self.get_dispatcher().register_bridge(dispatcher_entry)
        return entry, message

    # --------------------------------------------------------------- 原有能力
    def register_bridge(
        self, bridge_id: str, fact_table: str, dim_name: str,
        weight_ratio: float, row_count: int, version: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """登记桥接条目：含权重比例，后续归一校验。"""
        rows = store.rows(self.MODULE)
        entry = {
            "桥接编号": bridge_id, "所属事实表": fact_table,
            "关联维度": dim_name, "权重比例": str(weight_ratio),
            "桥接行数": row_count, "归一校验": "待校验",
            "桥接版本": version, "最近装配": "2026-11-21",
            "装配批次": "", "维度组": "", "派工指派人": "",
            "status": "待装配", "pending": True, "abnormal": False,
        }
        entry["id"] = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rows.append(entry)
        return True, f"桥接 {bridge_id} 已登记：{fact_table}→{dim_name} 权重 {weight_ratio}", entry

    def check_normalization(
        self, fact_table: str, row_weights: dict[int, float],
    ) -> tuple[bool, str, dict[str, Any]]:
        """归一校验：同一事实行关联的维度权重之和必须为1。归一失败时装配结果度量会被重复放大。"""
        failures: dict[int, float] = {}
        for row_id, total in row_weights.items():
            if abs(total - 1.0) > 0.001:
                failures[row_id] = total
        if failures:
            # 标记对应桥接条目为归一失败
            rows = store.rows(self.MODULE)
            for r in rows:
                if r.get("所属事实表") == fact_table:
                    r["归一校验"] = "失败"
                    r["status"] = "归一失败"
                    r["abnormal"] = True
            detail = ", ".join(f"行{k}={v:.3f}" for k, v in failures.items())
            return False, f"归一失败：{detail}", {"failures": failures}
        return True, f"全部桥接权重归一通过", {"ok": True}
