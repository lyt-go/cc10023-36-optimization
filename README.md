# 特征工程与数据挖掘工作台

面向特征构建、特征选择、数据切片、模型训练与挖掘结果管理的数据分析特征工程平台。

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

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 特征源管理 | `feature_source` | 特征源 | 源编号、源名称、数据表名 |
| 特征定义 | `feature_define` | 特征定义 | 特征编号、特征名称、特征类型 |
| 特征组管理 | `feature_group` | 特征组 | 组编号、组名称、包含特征 |
| 数据切片配置 | `data_slice` | 切片配置 | 切片编号、切片名称、数据来源 |
| 特征计算任务 | `feature_compute` | 计算任务 | 任务编号、关联特征、数据切片 |
| 特征筛选策略 | `feature_selection` | 筛选策略 | 策略编号、特征集、筛选方法 |
| 特征重要性分析 | `feature_iv` | 重要性评估 | 评估编号、关联特征、信息值IV |
| 特征快照管理 | `feature_snapshot` | 特征快照 | 快照编号、特征集、快照日期 |
| 训练流水线 | `train_pipeline` | 流水线配置 | 流水线编号、流水线名称、特征集 |
| 模型注册中心 | `model_registry` | 模型注册 | 模型编号、模型名称、算法类型 |
| 批量预测任务 | `predict_batch` | 预测任务 | 任务编号、模型编号、数据切片 |
| 挖掘结果管理 | `mining_result` | 挖掘结果 | 结果编号、分析主题、算法类型 |
| 聚类分析配置 | `cluster_config` | 聚类配置 | 配置编号、特征集、算法类型 |
| 特征相关性矩阵 | `feature_corr` | 相关性矩阵 | 矩阵编号、特征集、计算方法 |
| 特征漂移监控 | `drift_monitor` | 漂移监控 | 监控编号、关联特征、基准分布 |
| 数据导出管理 | `data_export` | 导出任务 | 任务编号、数据来源、导出范围 |
| 流水线调度 | `pipeline_schedule` | 调度配置 | 调度编号、关联流水线、Cron表达式 |
| 业务场景标注 | `scenario_label` | 场景标注 | 标注编号、场景名称、关联特征 |

## 评估分数校验与组验收

特征重要性指标里经常混入空值、空字符串、NaN、无穷或非数值。旧实现直接
`float(...)`，一条坏值就会让整批排序失败。现在的规则：

| 输入 | 状态 | 是否参与排行 |
| --- | --- | --- |
| `0` / `0.0` / `"0"` 等有效数值 | 有效 | 是，零分正常入榜 |
| `""` / `null` / 缺字段 | 待补分 | 否，保留原因 |
| `NaN` / `±Infinity` / 非数值 | 无效 | 否，保留原因（`nan`/`infinity`/`non_numeric`） |

打分与排行互不影响：单条坏值只标记自己，不会让整批失败；局部排行也
**不会**触发模型注册。

### 组验收

申请以登记的**必评样本清单**为准，`app/services/acceptance.py` 负责状态机：

- 每份必评样本都必须拿到有效分数且不低于 `0.7`，整组才通过；均值不作为放行依据；
- 本次验收中已确认有效的分数会被锁定，后续补录不能覆盖；
- 状态为 `待补证` 时可以继续补交缺失证据；
- 状态为 `已通过` / `不通过` 的申请已经结束，旧回执不会被后来的数据改写，需要新开验收；
- 策略 `early_close`（默认）：一旦某份已确认有效的必评样本低于门槛，立即结案为不通过；
  策略 `wait_complete`：保留待补证，等证据齐备后再因该低分结束；
- 只有整组通过才会把有效评估数据交给模型注册中心注册；未通过 / 待补证只登记
  一条「待验收」关联记录，并带上本次验收实际拿到的评估来源、状态、指标版本与例外记录。
- 例外记录会随补录推进：后来被修复的问题标为「已消解」，同组同值的重复异常不重复登记。
- 手工动作也不能绕过验收：`不通过` / `待补证` 的模型不能「确认注册」或部署。

前端的「模型评估验收」页（`/acceptance`）提供申请、提交 / 补录、提前结案、回执与
注册中心记录查看。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/feature_iv/scores/validate` | 校验一批评分，返回 `ranked/pending/invalid` |
| POST | `/api/feature_iv/scores/rank` | 同上，排行别名 |
| GET | `/api/feature_iv/rank?eval_ids=1,2,3` | 按评估编号排行并给出坏值原因 |
| POST | `/api/model_registry/acceptance` | 创建验收申请（登记必评样本清单） |
| POST | `/api/model_registry/acceptance/{id}/submit` | 提交 / 补录评分 |
| POST | `/api/model_registry/acceptance/{id}/close` | 提前结案 |
| GET | `/api/model_registry/acceptance/{id}/receipt` | 查询当次回执 |
| GET | `/api/model_registry/acceptance/{id}/exceptions` | 查询同组例外记录 |
| GET | `/api/model_registry/acceptance-record/{验收编号}` | 查询注册中心落地记录 |

`/api/acceptance/*` 是同一组能力的独立命名空间，便于单独调用。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
