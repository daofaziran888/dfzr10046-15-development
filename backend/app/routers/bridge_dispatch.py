"""桥接分派接口：把多值桥接条目跨模块派工成事实装配待办。

路由前缀 /api/bridge_link 与通用资源路由同前缀，但本文件的路径更具体
（多一段子路径），必须在 resource 之前注册，避免被 /api/{module} 动态抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.pipeline.bridge_dispatcher import DispatchError
from app.schemas import DispositionPayload, DispatchPayload, ResumePayload
from app.services.bridge_link import BridgeLinkService

router = APIRouter(prefix="/api/bridge_link", tags=["桥接分派"])

service = BridgeLinkService()


@router.get("/dispatch/overview")
def dispatch_overview(
    assembly_batch: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """派工台：逐条展示两批归属、处置资格、在途待办与拒绝依据。"""
    return service.dispatch_overview(assembly_batch)


@router.get("/dispatch/entries/{assembly_batch}/{bridge_no}")
def entry_view(assembly_batch: str, bridge_no: str) -> dict[str, Any]:
    """单条目视图：同编号跨批次归属、版本、在途待办、逐人资格明细。"""
    try:
        return service.entry_dispatch_view(assembly_batch, bridge_no)
    except DispatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/dispatch/eligibility/{assembly_batch}/{bridge_no}")
def eligibility(assembly_batch: str, bridge_no: str) -> dict[str, Any]:
    """逐条查看谁有下一步处置资格以及其他人的拒绝依据。"""
    try:
        return service.eligibility(assembly_batch, bridge_no)
    except DispatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/assembly-todos")
def assembly_todos(
    assembly_batch: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """跨模块生成的事实装配待办。"""
    return service.assembly_todos(assembly_batch)


@router.get("/receipts")
def receipts(
    request_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """回执台账：审核员据此分辨条目由谁完成、冲突为何被拒。"""
    return service.receipts(request_id)


@router.post("/dispatch")
def dispatch_entries(payload: DispatchPayload) -> dict[str, Any]:
    """批量派工/改派：审核员多选同一批次的多条条目一次派给一个装配员。"""
    items = [item.model_dump() for item in payload.items]
    return service.dispatch_entries(
        items, payload.assembler, payload.assembly_batch, payload.request_id)


@router.post("/dispositions")
def submit_dispositions(payload: DispositionPayload) -> dict[str, Any]:
    """装配员提交处分：并发冲突时按版本仲裁，逐条只确认一项。"""
    items = [item.model_dump() for item in payload.items]
    return service.submit_dispositions(
        items, payload.assembler, payload.assembly_batch, payload.request_id)


@router.post("/resume")
def resume_request(payload: ResumePayload) -> dict[str, Any]:
    """回包丢失后凭请求编号重试：只接续原操作，不重复执行。"""
    try:
        return service.resume_request(payload.request_id)
    except DispatchError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
