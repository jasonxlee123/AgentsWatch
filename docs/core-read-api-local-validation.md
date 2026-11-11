# 核心只读 API 本机验证报告

> 文档状态：2026-07-12 阶段性验证快照，只证明当时的只读接口范围。当前 API 已包含 CRUD、批量导入、任务、认证和运维集成；现行能力以受认证 OpenAPI 和[系统架构](system-architecture.md)为准。

## 1. 结论

核心资产只读 API 已在本机完成开发和集成验证。服务使用 FastAPI、Uvicorn 和 psycopg，连接本机临时 PostgreSQL 16，仅提供查询能力。

本阶段没有连接生产数据库、生产服务器或真实 Runtime，也没有开放新增、修改、退役、导入提交和批量操作。

验证日期：2026-07-12。

## 2. 核心接口

| 接口 | 用途 |
| --- | --- |
| `GET /healthz` | 数据库连通性、数据库用户和只读事务状态 |
| `GET /api/v1/overview` | Server、Runtime、Agent、Deployment、管理组和数据质量汇总 |
| `GET /api/v1/reference-data` | Environment、CloudProvider、RuntimeFamily 和 Department 字典 |
| `GET /api/v1/people` | 人员工号、姓名和部门分页查询 |
| `GET /api/v1/servers` | Server 搜索、状态/环境/管理组筛选和分页 |
| `GET /api/v1/servers/{id}` | Server 配置、Runtime 和管理组关系 |
| `GET /api/v1/runtimes` | Runtime Family、部署模式、状态和 Server 筛选 |
| `GET /api/v1/runtimes/{id}` | Runtime、活动 Primary Agent 和最新发现快照 |
| `GET /api/v1/agents` | 主子结构、服务类型、状态、部门和管理组筛选 |
| `GET /api/v1/agents/{id}` | 管理员、服务对象、Subagent、访问用户、部署和网络需求 |
| `GET /api/v1/deployments` | AgentDeployment 当前与历史查询 |
| `GET /api/v1/groups` | Server/Agent 虚拟管理组及当前成员数量 |
| `GET /api/v1/groups/{id}` | 管理分组详情及按资源类型返回的当前 Server/主 Agent 成员 |
| `GET /api/v1/imports` | 导入批次分页查询 |
| `GET /api/v1/imports/{id}` | 暂存行、问题和字段差异详情 |
| `GET /api/v1/data-quality/issues` | 数据质量问题筛选和分页，包含导入行对象、版本、原始载荷和归一化载荷 |

FastAPI 自动提供本机 OpenAPI 文档 `/docs` 和 `/openapi.json`。

## 3. 查询约定

- 列表接口统一返回 `data` 和 `page={limit,offset,total}`。
- `limit` 范围为 1..100，默认 50。
- 搜索和筛选全部使用参数化 SQL。
- 生命周期、服务类型、部署模式、资源类型和严重级别使用 API 枚举校验；非法值返回 422，不进入数据库。
- UUID 路径和筛选参数由框架校验。
- 详情对象不存在时返回 404。
- 数据库不可用时返回 503，不暴露 SQL 和连接信息。

## 4. 只读边界

只读由三层共同保证：

1. API 只注册 GET 路由，POST/PUT/PATCH/DELETE 返回 405。
2. 测试数据库账号 `agentswatch_reader` 只有 Schema USAGE 和表 SELECT 权限。
3. 数据库角色默认 `default_transaction_read_only=on`，每次 API 连接再次执行 `SET TRANSACTION READ ONLY`。

集成测试使用 reader 账号直接执行 INSERT，PostgreSQL 明确以 read-only transaction 拒绝。

## 5. 本机集成环境

测试脚本执行以下流程：

1. 启动临时 PostgreSQL 16，并仅将随机端口绑定到 `127.0.0.1`。
2. 创建核心 Schema 并装载全模拟资产数据。
3. 创建最小权限 reader 角色。
4. 在随机本机端口启动 API。
5. 执行真实 HTTP 请求和数据库查询。
6. 测试结束后停止 API 并删除数据库容器和临时文件。

运行：

```bash
python3 -m venv .venv-api
.venv-api/bin/pip install -r requirements-api.txt
./scripts/test_core_api_local.sh
```

## 6. 已验证场景

- 健康检查确认数据库事务为只读。
- 总览数量与模拟主数据一致。
- Server 状态筛选、IP 搜索、分页及详情关系。
- OpenClaw Runtime 筛选、唯一活动 Primary Agent 以及 Gateway/服务器展示字段。
- Agent 主体/服务类型筛选、Subagent、多人访问和活动部署。
- 管理组列表、Server/Agent 类型化成员详情、部署、人员、字典和数据质量查询。
- 导入批次的行、问题和字段差异详情，以及问题行原始/归一化载荷。
- 非法分页和非法枚举返回 422。
- 不存在对象返回 404。
- HTTP 写方法返回 405。
- 数据库 reader 账号不能写入。

## 7. 当前边界

- 本报告生成时尚未包含身份认证；当前管理 API 已补充用户会话、滑块验证和 `admin` / `operator` / `viewer` 权限控制。
- 没有写 API、导入确认、退役、迁移或批量任务。
- 没有连接生产资产、生产组织数据或 Runtime。
- 没有管理台页面。
- 没有生产部署、连接池、限流和性能压测。

下一阶段应在本机开发最小管理台，先实现总览、Server、Runtime、Agent 和数据质量页面，只消费当前只读 API。
