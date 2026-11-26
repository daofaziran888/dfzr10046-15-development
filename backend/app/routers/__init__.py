"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import bridge_dispatch, resource

# 桥接分派的具体路径需排在通用 /api/{module} 动态路由之前注册。
ROUTERS = [bridge_dispatch, resource]
