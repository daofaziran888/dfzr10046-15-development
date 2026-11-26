# 维链工坊 · 维度建模与缓慢变化维加工平台

面向维度注册、缓慢变化维拉链、一致性维度核对、事实表装配、代理键生成、维度退化、迟到事实回补、汇总层派生、指标口径绑定与维度快照发布的数仓维度建模加工平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8003/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5176/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8003`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 维度建档 | `dim_entry` | 维度条目 | 维度编号、维度名称、业务主键 |
| 拉链加工 | `scd_chain` | 拉链记录 | 拉链编号、所属维度、代理键 |
| 代理键生成 | `surrogate_key` | 代理键段 | 键段编号、所属维度、键段起点 |
| 一致性核对 | `conform_check` | 核对条目 | 核对编号、基准维度、对照维度 |
| 事实装配 | `fact_assemble` | 事实批次 | 批次编号、所属事实表、度量列集合 |
| 退化维度 | `degenerate_dim` | 退化维条目 | 条目编号、所属事实表、退化字段 |
| 迟到事实 | `late_fact` | 迟到记录 | 记录编号、所属事实表、业务发生日 |
| 汇总层派生 | `summary_layer` | 汇总条目 | 汇总编号、来源事实表、汇总粒度 |
| 粒度声明 | `grain_declare` | 粒度声明 | 声明编号、所属事实表、一行代表 |
| 度量目录 | `measure_catalog` | 度量条目 | 度量编号、度量名称、计算表达式 |
| 指标绑定 | `metric_bind` | 绑定关系 | 绑定编号、指标名称、引用度量 |
| 维度快照 | `dim_snapshot` | 快照版本 | 快照编号、所属维度、快照日期 |
| 层级路径 | `hier_path` | 层级路径 | 路径编号、所属维度、层级名称 |
| 多值桥接 | `bridge_link` | 桥接条目 | 桥接编号、所属事实表、关联维度 |
| 属性失效 | `attr_decay` | 失效条目 | 失效编号、所属维度、属性名称 |
| 装载窗口 | `load_window` | 装载窗口 | 窗口编号、所属事实表、窗口起点 |
| 重跑任务 | `reprocess_task` | 重跑任务 | 重跑编号、触发原因、影响事实表 |
| 装配质量闸 | `quality_gate` | 质量闸条目 | 闸口编号、所属事实表、校验项 |
| 血缘关系 | `lineage_edge` | 血缘边 | 血缘编号、上游对象、下游对象 |
| 维度发布 | `dim_publish` | 发布版本 | 发布编号、所属维度、发布版本 |
| 建模审计 | `model_audit` | 审计条目 | 条目编号、操作类型、操作对象 |
| 源端抽取 | `src_extract` | 抽取批次 | 抽取编号、来源系统、抽取范围 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 桥接分派（多值桥接 → 事实装配待办）

多值桥接条目按事实批次跨模块派工，入口在前端「桥接分派」页签
（`/bridge_dispatch`，`frontend/src/views/BridgeDispatchView.vue`），
领域引擎在 `backend/app/pipeline/bridge_dispatcher.py`，服务编排在
`backend/app/services/bridge_link.py`，路由在
`backend/app/routers/bridge_dispatch.py`。

- 同一桥接编号可分属不同事实批次（F90 门店组、F91 渠道组两个页签里的 BRG-07
  是两条独立条目），各自派工到各自装配批次，互不串单。
- 审核员多选同一批次多条条目批量派工；逐条返回每个装配员的处置资格与拒绝依据
  （资格按维度组判定，归一失败/已作废条目全员被拒，其他条目照常派工）。
- 在途未写入时改派：旧待办置「已改派」、条目版本号前进；并发提交处分时凭版本号
  乐观仲裁，同一条目只确认一项处分，另一项带回拒绝依据。
- 回包丢失凭原 `request_id` 调 `/resume` 幂等接续，不重复生成待办；回执台账
  可分辨每条桥接最终由谁完成。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/bridge_link/dispatch/overview` | 派工台：两批归属、资格、在途待办、最近拒绝依据 |
| GET | `/api/bridge_link/dispatch/eligibility/{批次}/{桥接编号}` | 逐人处置资格与拒绝依据 |
| GET | `/api/bridge_link/dispatch/entries/{批次}/{桥接编号}` | 单条目视图（含同编号跨批归属） |
| GET | `/api/bridge_link/assembly-todos` | 跨模块生成的事实装配待办 |
| GET | `/api/bridge_link/receipts` | 回执台账 |
| POST | `/api/bridge_link/dispatch` | 批量派工/改派 |
| POST | `/api/bridge_link/dispositions` | 装配员处分批量提交（版本仲裁） |
| POST | `/api/bridge_link/resume` | 凭请求编号幂等接续重试 |

领域：数据工程
子方向：数仓与数据湖
