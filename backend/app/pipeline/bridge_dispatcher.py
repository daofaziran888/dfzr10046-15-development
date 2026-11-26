"""桥接分派引擎：把多值桥接条目跨模块派工成事实装配待办。

业务背景：
- 桥接审核员在多值桥接模块审核条目，但多值桥接本身没有“按事实批次派工”的入口，
  装配员拿不到待办。本引擎负责把桥接条目分派给有处置资格的装配员，生成
  fact_assemble（事实装配）侧的待办。
- 同一桥接编号可以出现在不同事实批次（两个页签/两个模块）里，例如 BRG-07 同时
  属于 F90（门店组）与 F91（渠道组），两条条目各自独立派工到各自的装配批次，
  互不串单。
- 改派与在途请求冲突：条目已派给甲且处置结果尚未写入（在途）时改派给乙，
  采用版本号乐观并发控制，并发提交处分时只有一项能确认，另一项按拒绝依据驳回。
- 回包丢失后凭原请求编号重试，只接续原操作（幂等），审核员可凭回执分辨条目
  最终由谁完成。

本引擎只依赖标准库，状态保存在内存中，与 app.store 解耦，便于单元测试；
服务层（BridgeLinkService）负责把内存仓库中的桥接条目注册进来并同步状态。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


class DispatchError(Exception):
    """桥接分派领域错误（参数/状态非法），与“条目级拒绝回执”区分开。"""


# 装配待办与桥接条目的状态常量
TODO_IN_FLIGHT = "在途"
TODO_CONFIRMED = "已确认"
TODO_SUPERSEDED = "已改派"

BRIDGE_TERMINAL_STATUSES = ("已装配", "归一失败", "已作废")


@dataclass
class BridgeEntry:
    """一条可被分派的桥接条目（由所属事实批次 + 桥接编号唯一确定）。"""

    assembly_batch: str          # 事实装配批次，如 F90
    bridge_no: str               # 桥接编号，如 BRG-07
    fact_table: str              # 所属事实表
    dim_group: str               # 关联维度组，决定需要哪个装配组的资格
    version: int = 0             # 乐观并发版本，每次成功派工/改派 +1
    status: str = "待装配"        # 待装配 / 已装配 / 归一失败 / 已作废
    normalization: str = "待校验"  # 归一校验：待校验 / 通过 / 失败
    current_assignee: str | None = None  # 当前持有在途待办的装配员
    dispatch_seq: int = 0        # 累计派工次数，用于区分同一批同编号条目

    @property
    def key(self) -> tuple[str, str]:
        return self.assembly_batch, self.bridge_no

    def is_dispatchable(self) -> tuple[bool, str]:
        """条目本身是否处于可派工状态。"""
        if self.status == "已作废":
            return False, "桥接条目已作废，不再派工"
        if self.status == "已装配":
            return False, "桥接条目已装配完成，无需重复派工"
        if self.status == "归一失败" or self.normalization == "失败":
            return False, "桥接权重归一校验失败，需先修正权重再派工"
        return True, ""


@dataclass
class AssemblyTodo:
    """跨模块生成的事实装配待办。"""

    todo_id: str
    request_id: str
    assembly_batch: str
    bridge_no: str
    fact_table: str
    assignee: str
    version: int                 # 派工时条目的版本，处分时必须与条目当前版本一致
    seq: int                     # 第几次派工
    status: str = TODO_IN_FLIGHT  # 在途 / 已确认 / 已改派
    disposition: str | None = None  # 装配完成 / 拒绝处置
    completed_by: str | None = None
    completed_request: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "待办编号": self.todo_id,
            "请求编号": self.request_id,
            "装配批次": self.assembly_batch,
            "桥接编号": self.bridge_no,
            "所属事实表": self.fact_table,
            "指派装配员": self.assignee,
            "条目版本": self.version,
            "派工次序": self.seq,
            "待办状态": self.status,
            "处置结论": self.disposition or "",
            "完成人": self.completed_by or "",
            "完成请求": self.completed_request or "",
        }


@dataclass
class DispatchReceipt:
    """每次派工/处分/重试的回执：成功与否、版本与拒绝依据都留痕。"""

    receipt_id: str
    request_id: str
    operation: str              # dispatch / disposition / retry
    assembly_batch: str
    bridge_no: str
    assignee: str
    ok: bool
    version_before: int
    version_after: int
    reject_reason: str = ""
    todo_id: str = ""
    detail: str = ""
    resumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "回执编号": self.receipt_id,
            "请求编号": self.request_id,
            "操作类型": self.operation,
            "装配批次": self.assembly_batch,
            "桥接编号": self.bridge_no,
            "装配员": self.assignee,
            "是否成功": "是" if self.ok else "否",
            "版本前": self.version_before,
            "版本后": self.version_after,
            "拒绝依据": self.reject_reason,
            "待办编号": self.todo_id,
            "说明": self.detail,
            "是否接续重试": "是" if self.resumed else "否",
        }


@dataclass
class _BatchRecord:
    """一次批量请求（派工或处分）的完整结果，供回包丢失后幂等接续。"""

    request_id: str
    operation: str
    batch_id: str
    receipts: list[DispatchReceipt] = field(default_factory=list)
    resumed: bool = False

    def result(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "operation": self.operation,
            "assembly_batch": self.batch_id,
            "resumed": self.resumed,
            "success_count": sum(1 for r in self.receipts if r.ok),
            "reject_count": sum(1 for r in self.receipts if not r.ok),
            "items": [r.to_dict() for r in self.receipts],
        }


class BridgeDispatcher:
    """桥接分派器：资格判定、批量派工、并发处分冲突仲裁与幂等重试。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._assemblers: dict[str, set[str]] = {}   # 装配员 → 可处置的维度组
        self._entries: dict[tuple[str, str], BridgeEntry] = {}
        self._todos: list[AssemblyTodo] = []
        self._receipts: list[DispatchReceipt] = []
        self._requests: dict[str, _BatchRecord] = {}
        self._seq = 0

    # ------------------------------------------------------------------ 注册
    def register_assembler(self, name: str, groups: list[str]) -> None:
        """登记装配员对哪些维度组（门店组/渠道组…）有下一步处置资格。"""
        with self._lock:
            self._assemblers[name] = set(groups)

    def register_bridge(self, entry: BridgeEntry) -> None:
        """注册桥接条目；已存在（同批次同编号）时仅同步可派工状态，不抹掉派工痕迹。"""
        with self._lock:
            existing = self._entries.get(entry.key)
            if existing is None:
                self._entries[entry.key] = entry
            else:
                existing.status = entry.status
                existing.normalization = entry.normalization

    # ------------------------------------------------------------------ 资格
    def eligibility_of(self, assembly_batch: str, bridge_no: str) -> dict[str, Any]:
        """逐条返回每个装配员对该条目的处置资格与拒绝依据。"""
        with self._lock:
            entry = self._entries.get((assembly_batch, bridge_no))
            if entry is None:
                raise DispatchError(
                    f"桥接条目 {assembly_batch}/{bridge_no} 未登记，无法判定资格")
            rows = []
            for name in sorted(self._assemblers):
                ok, reason = self._qualify(entry, name)
                rows.append({
                    "装配员": name,
                    "有资格": "是" if ok else "否",
                    "拒绝依据": reason,
                })
            return {
                "装配批次": assembly_batch,
                "桥接编号": bridge_no,
                "维度组": entry.dim_group,
                "条目状态": entry.status,
                "当前版本": entry.version,
                "当前指派人": entry.current_assignee or "",
                "资格明细": rows,
            }

    def _qualify(self, entry: BridgeEntry, assembler: str) -> tuple[bool, str]:
        """单个装配员的资格判定，返回 (是否有资格, 拒绝依据)。"""
        groups = self._assemblers.get(assembler)
        if groups is None:
            return False, f"装配员 {assembler} 未登记处置资格"
        if entry.dim_group not in groups:
            return False, (
                f"{assembler}仅持有 {'/'.join(sorted(groups)) or '无'} 资格，"
                f"该条目需要 {entry.dim_group} 处置资格"
            )
        ok, reason = entry.is_dispatchable()
        if not ok:
            return False, reason
        return True, ""

    # --------------------------------------------------------------- 派工/改派
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _dispatch_one(
        self, assembly_batch: str, bridge_no: str, assembler: str, request_id: str,
    ) -> DispatchReceipt:
        entry = self._entries.get((assembly_batch, bridge_no))
        if entry is None:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "dispatch",
                assembly_batch, bridge_no, assembler, False, -1, -1,
                reject_reason=f"桥接条目 {assembly_batch}/{bridge_no} 未登记",
                detail="条目不存在，已跳过",
            )

        version_before = entry.version
        ok, reason = self._qualify(entry, assembler)
        if not ok:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "dispatch",
                assembly_batch, bridge_no, assembler, False,
                version_before, version_before, reject_reason=reason,
            )

        # 在途冲突处理：旧待办标记“已改派”，条目版本前进，旧处分随后会被版本拦截。
        previous = self._active_todo(assembly_batch, bridge_no)
        previous_assignee = entry.current_assignee
        if previous is not None:
            previous.status = TODO_SUPERSEDED
        entry.dispatch_seq += 1
        entry.version += 1
        entry.current_assignee = assembler
        todo = AssemblyTodo(
            todo_id=self._next_id("TODO"),
            request_id=request_id,
            assembly_batch=assembly_batch,
            bridge_no=bridge_no,
            fact_table=entry.fact_table,
            assignee=assembler,
            version=entry.version,
            seq=entry.dispatch_seq,
        )
        self._todos.append(todo)
        detail = (
            f"改派：原在途指派人 {previous_assignee or '无'}，"
            f"其在途请求将在处分时按版本冲突驳回"
            if previous is not None else "首次派工，已生成事实装配待办"
        )
        return DispatchReceipt(
            self._next_id("RCP"), request_id, "dispatch",
            assembly_batch, bridge_no, assembler, True,
            version_before, entry.version, todo_id=todo.todo_id, detail=detail,
        )

    def dispatch_batch(
        self,
        items: list[dict[str, str]],
        assembler: str,
        assembly_batch: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """批量派工：审核员多选同一批次的多条条目一次派给同一装配员。

        逐条独立判定：无资格/异常条目产生拒绝回执，其他条目照常派工。
        request_id 相同的重复调用直接返回原结果（幂等，不重复生成待办）。
        """
        with self._lock:
            request_id = request_id or f"REQ-{uuid.uuid4().hex[:12]}"
            cached = self._requests.get(request_id)
            if cached is not None and cached.operation == "dispatch":
                cached.resumed = True
                return self._mark_resumed(cached).result()

            record = _BatchRecord(request_id, "dispatch", assembly_batch)
            for item in items:
                record.receipts.append(self._dispatch_one(
                    item["assembly_batch"], item["bridge_no"], assembler, request_id,
                ))
            self._requests[request_id] = record
            self._receipts.extend(record.receipts)
            return record.result()

    # --------------------------------------------------------------- 处分确认
    def _active_todo(
        self, assembly_batch: str, bridge_no: str,
    ) -> AssemblyTodo | None:
        for todo in reversed(self._todos):
            if (todo.assembly_batch == assembly_batch
                    and todo.bridge_no == bridge_no
                    and todo.status == TODO_IN_FLIGHT):
                return todo
        return None

    def _disposition_one(
        self,
        assembly_batch: str,
        bridge_no: str,
        assembler: str,
        disposition: str,
        request_id: str,
        client_version: int | None,
    ) -> DispatchReceipt:
        entry = self._entries.get((assembly_batch, bridge_no))
        if entry is None:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "disposition",
                assembly_batch, bridge_no, assembler, False, -1, -1,
                reject_reason=f"桥接条目 {assembly_batch}/{bridge_no} 未登记",
            )
        version_before = entry.version
        todo = self._active_todo(assembly_batch, bridge_no)
        if todo is None:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "disposition",
                assembly_batch, bridge_no, assembler, False,
                version_before, version_before,
                reject_reason="该条目没有在途装配待办（尚未派工或已确认/已改派）",
            )
        if todo.assignee != assembler:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "disposition",
                assembly_batch, bridge_no, assembler, False,
                version_before, version_before,
                reject_reason=(
                    f"在途待办当前指派给 {todo.assignee}，{assembler} 不是受让人，"
                    "无权提交处分"
                ),
            )
        # 派工后条目被作废/判归一失败/已装配的，在途处分不再允许确认。
        dispatchable, block_reason = entry.is_dispatchable()
        if not dispatchable:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "disposition",
                assembly_batch, bridge_no, assembler, False,
                version_before, version_before, todo_id=todo.todo_id,
                reject_reason=f"处分拒绝确认：{block_reason}",
            )
        # 乐观并发：改派后条目版本已前进，旧受让人持有的在途请求必然版本不符。
        if client_version is not None and client_version != todo.version:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "disposition",
                assembly_batch, bridge_no, assembler, False,
                version_before, version_before, todo_id=todo.todo_id,
                reject_reason=(
                    f"版本冲突：请求基于版本 {client_version}，条目已改派至版本 "
                    f"{entry.version}（当前受让人 {entry.current_assignee}），"
                    "本次处分拒绝确认"
                ),
            )
        if client_version is None and entry.version != todo.version:
            return DispatchReceipt(
                self._next_id("RCP"), request_id, "disposition",
                assembly_batch, bridge_no, assembler, False,
                version_before, version_before, todo_id=todo.todo_id,
                reject_reason=(
                    f"版本冲突：在途待办基于版本 {todo.version}，条目当前版本 "
                    f"{entry.version}，疑似改派后的迟到回包，拒绝确认"
                ),
            )

        todo.status = TODO_CONFIRMED
        todo.disposition = disposition
        todo.completed_by = assembler
        todo.completed_request = request_id
        if disposition == "装配完成":
            entry.status = "已装配"
        entry.current_assignee = None
        return DispatchReceipt(
            self._next_id("RCP"), request_id, "disposition",
            assembly_batch, bridge_no, assembler, True,
            version_before, entry.version, todo_id=todo.todo_id,
            detail=f"处分已确认：{disposition}，完成人 {assembler}",
        )

    def submit_disposition_batch(
        self,
        items: list[dict[str, Any]],
        assembler: str,
        assembly_batch: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """装配员并发提交处分：逐条仲裁，同一条目只有一项处分能确认。"""
        with self._lock:
            request_id = request_id or f"REQ-{uuid.uuid4().hex[:12]}"
            cached = self._requests.get(request_id)
            if cached is not None and cached.operation == "disposition":
                cached.resumed = True
                return self._mark_resumed(cached).result()

            record = _BatchRecord(request_id, "disposition", assembly_batch)
            for item in items:
                record.receipts.append(self._disposition_one(
                    item["assembly_batch"],
                    item["bridge_no"],
                    assembler,
                    str(item.get("disposition", "装配完成")),
                    request_id,
                    item.get("version"),
                ))
            self._requests[request_id] = record
            self._receipts.extend(record.receipts)
            return record.result()

    # ------------------------------------------------------------------ 重试
    def _mark_resumed(self, record: _BatchRecord) -> _BatchRecord:
        record.resumed = True
        for receipt in record.receipts:
            receipt.resumed = True
        return record

    def resume(self, request_id: str) -> dict[str, Any]:
        """回包丢失后凭原请求编号重试：只接续原操作，绝不重复执行。"""
        with self._lock:
            cached = self._requests.get(request_id)
            if cached is None:
                raise DispatchError(
                    f"请求编号 {request_id} 不存在或已过期，无法接续；"
                    "请确认回执上的请求编号"
                )
            return self._mark_resumed(cached).result()

    # ------------------------------------------------------------------ 视图
    def entry_view(self, assembly_batch: str, bridge_no: str) -> dict[str, Any]:
        """单条目视图：两批归属、当前版本、在途/完成状态与资格明细。"""
        with self._lock:
            entry = self._entries.get((assembly_batch, bridge_no))
            if entry is None:
                raise DispatchError(
                    f"桥接条目 {assembly_batch}/{bridge_no} 未登记")
            todo = self._active_todo(assembly_batch, bridge_no)
            eligibility = self.eligibility_of(assembly_batch, bridge_no)
            # 同编号条目跨批次归属：让审核员一眼看到 BRG-07 在两个页签各自的归属。
            peers = [
                {
                    "装配批次": other.assembly_batch,
                    "所属事实表": other.fact_table,
                    "维度组": other.dim_group,
                    "条目状态": other.status,
                    "当前指派人": other.current_assignee or "",
                }
                for other in self._entries.values()
                if other.bridge_no == bridge_no and other.key != entry.key
            ]
            return {
                "装配批次": assembly_batch,
                "桥接编号": bridge_no,
                "所属事实表": entry.fact_table,
                "维度组": entry.dim_group,
                "条目状态": entry.status,
                "归一校验": entry.normalization,
                "当前版本": entry.version,
                "当前指派人": entry.current_assignee or "",
                "在途待办": todo.to_dict() if todo else None,
                "同编号跨批归属": peers,
                "资格明细": eligibility["资格明细"],
            }

    def dispatch_overview(self, assembly_batch: str | None = None) -> list[dict[str, Any]]:
        """派工台列表：逐条给出两批归属、资格与在途状态。"""
        with self._lock:
            views = []
            for (batch, bridge_no) in sorted(self._entries):
                if assembly_batch and batch != assembly_batch:
                    continue
                entry = self._entries[(batch, bridge_no)]
                todo = self._active_todo(batch, bridge_no)
                eligible = [
                    row["装配员"] for row in
                    self.eligibility_of(batch, bridge_no)["资格明细"]
                    if row["有资格"] == "是"
                ]
                # 该条目最近一条拒绝依据（派工/处分被驳回时可逐条展示）。
                last_reject = ""
                for receipt in reversed(self._receipts):
                    if (receipt.assembly_batch == batch
                            and receipt.bridge_no == bridge_no
                            and not receipt.ok):
                        last_reject = receipt.reject_reason
                        break
                same_no_batches = sorted({
                    other.assembly_batch
                    for other in self._entries.values()
                    if other.bridge_no == bridge_no
                })
                views.append({
                    "id": f"{batch}-{bridge_no}",
                    "装配批次": batch,
                    "桥接编号": bridge_no,
                    "所属事实表": entry.fact_table,
                    "维度组": entry.dim_group,
                    "归一校验": entry.normalization,
                    "条目状态": entry.status,
                    "当前版本": entry.version,
                    "当前指派人": entry.current_assignee or "",
                    "待办状态": todo.status if todo else "",
                    "待办编号": todo.todo_id if todo else "",
                    "有资格装配员": "、".join(eligible) or "（无人具备资格）",
                    "同编号批次归属": "、".join(same_no_batches),
                    "最近拒绝依据": last_reject,
                })
            return views

    def todos(self, assembly_batch: str | None = None) -> list[dict[str, Any]]:
        """事实装配待办列表（跨模块产物，供事实装配侧消费）。"""
        with self._lock:
            return [
                todo.to_dict() for todo in self._todos
                if assembly_batch is None or todo.assembly_batch == assembly_batch
            ]

    def receipts(self, request_id: str | None = None) -> list[dict[str, Any]]:
        """回执台账：审核员据此分辨条目最终由谁完成、为何被拒。"""
        with self._lock:
            return [
                receipt.to_dict() for receipt in self._receipts
                if request_id is None or receipt.request_id == request_id
            ]
