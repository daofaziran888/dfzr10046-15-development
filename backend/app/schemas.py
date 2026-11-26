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


class DispatchItem(BaseModel):
    """单条派工目标：事实批次 + 桥接编号唯一定位一条桥接条目。"""

    assembly_batch: str
    bridge_no: str


class DispatchPayload(BaseModel):
    """桥接批量派工请求。"""

    items: list[DispatchItem]
    assembler: str
    assembly_batch: str
    request_id: str | None = None


class DispositionItem(BaseModel):
    """单条处分提交：携带派工时的条目版本做乐观并发控制。"""

    assembly_batch: str
    bridge_no: str
    disposition: str = "装配完成"
    version: int | None = None


class DispositionPayload(BaseModel):
    """装配员处分批量提交请求。"""

    items: list[DispositionItem]
    assembler: str
    assembly_batch: str
    request_id: str | None = None


class ResumePayload(BaseModel):
    """回包丢失后的幂等重试请求。"""

    request_id: str
