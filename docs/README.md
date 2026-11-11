# AgentsWatch 文档中心

这里是项目文档的统一入口。第一次了解系统时，按“产品说明 → 系统架构 → 部署指引”的顺序阅读；开发或排障时，再进入对应的专项文档。

## 首要文档

| 文档 | 用途 | 状态 |
| --- | --- | --- |
| [项目总览](../README.md) | 产品定位、模块能力、本机快速启动 | 当前 |
| [系统架构](system-architecture.md) | 模块边界、组件、数据流、安全与可用性设计 | 当前、架构基线 |
| [部署指引](deployment-guide.md) | 同时部署、仅管理、仅运维三种生产拓扑 | 当前、部署总入口 |
| [本机联调环境](local-management-stack.md) | 开发机上的 PostgreSQL、API、MCP、适配器和前端 | 当前、仅开发/测试 |

“当前”表示文档描述的是现行实现；“专项参考”表示只覆盖一个子系统；“验证快照”记录特定时间的测试证据，不代表当前完整能力。

## 按主题查找

### 架构与数据

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [核心资产数据架构](core-data-architecture.md) | Server、Runtime、Agent、Deployment、管理分组和组织关系 | 当前、专项参考 |
| [核心资产数据字典](core-data-dictionary.md) | PostgreSQL 表、字段、枚举、敏感级别和运维只读契约 | 当前、专项参考 |
| [遥测数据持久化](data-persistence.md) | 指标、链路、日志、ClickHouse 分析副本和保留周期 | 当前、专项参考 |
| [Grafana 看板与运营报表](grafana-dashboards.md) | 看板分层、查询口径和报表操作 | 当前、专项参考 |

数据库的最终权威是 [`database/core-assets`](../database/core-assets/README.md) 下的有序迁移；字段文档用于解释语义，不能代替迁移文件。REST 接口的最终权威是运行中管理 API 的受认证 OpenAPI 文档。

### 管理与集成

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [资产批量导入契约](asset-import-contract.md) | Excel 校验、预览、差异和事务提交语义 | 当前 |
| [批量服务器首次纳管](bulk-server-onboarding.md) | 生成、核验并执行安全的 SSH 首次纳管计划 | 当前、专项参考 |
| [标准任务执行流程](automation-task-execution.md) | Playbook 发布、任务预检、执行事件和逐目标结果 | 当前、专项参考 |
| [开放 API 访问契约](open-api-access.md) | API Key、角色权限、二次确认和审计 | 当前 |
| [MCP 服务](mcp-service.md) | MCP 启动、认证和 REST 封装边界 | 当前 |
| [Runtime WebUI 访问](runtime-web-access.md) | SSH 隧道、Tailscale Serve 和私网访问 | 当前、专项参考 |
| [自动化库维护](../automation/README.md) | 内置 Playbook、参数 Schema 和发布约束 | 当前、代码邻近参考 |

### 部署与运维

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [统一部署指引](deployment-guide.md) | 三种部署模式的选择、配置、启动、验收和回滚 | 当前、权威入口 |
| [联合部署覆盖](../deploy/combined/README.md) | 统一 Nginx、适配器容器与双后端网络编排 | 当前、代码邻近参考 |
| [管理模块生产部署](../deploy/management/README.md) | 管理镜像、UDS、专属 Nginx 和测试直部署细节 | 当前、专项参考 |
| [运维控制适配器](../agentswatch_observability/README.md) | revision、API、CLI、Overlay 和 Prometheus 动态配置 | 当前、专项参考 |
| [离线镜像包](../deploy/offline/README.md) | 当前运维栈镜像的导出、加载和校验 | 站点专项参考 |
| [本地 JSON 清单](../cluster/README.md) | 无管理数据库时的兼容清单、Token 和配置下发 | 运维独立模式参考 |

根运维 Compose 不包含 `agentswatch_observability`。联合覆盖会加入适配器容器；其端口和统一 Nginx 的内部入口都不发布到宿主，预期由管理 API 携带强制 Bearer Token 调用。仅运维模式若长期开放适配器 API，才需要单独 supervisor 与安全入口。

### 验证记录

下列文档是阶段性验证证据。它们说明测试当时证明了什么，不应被用来判断当前功能是否仍“尚未实现”。

| 文档 | 验证范围 | 状态 |
| --- | --- | --- |
| [核心数据接入本机验证](core-data-ingest-local-validation.md) | Excel 暂存、迁移预览、模拟 Runtime 发现 | 验证快照 |
| [核心数据模型模拟验证](core-data-model-validation.md) | JSON 模型、关系约束和迁移演练 | 验证快照 |
| [核心资产 PostgreSQL 验证](core-data-postgresql-validation.md) | PostgreSQL 16 DDL、约束与并发 | 验证快照 |
| [核心 API 本机验证](core-read-api-local-validation.md) | 当时的 API 集成测试范围 | 验证快照，能力可能已扩展 |

## 三种推荐阅读路径

### 系统评审

1. [项目总览](../README.md)
2. [系统架构](system-architecture.md)
3. [部署指引](deployment-guide.md)中的部署矩阵与安全检查
4. [核心资产数据架构](core-data-architecture.md)和[遥测数据持久化](data-persistence.md)

### 生产部署

1. 在[部署指引](deployment-guide.md)选择三种模式之一
2. 完成该模式的前置检查、配置和验收
3. 需要实现细节时进入管理、适配器或离线镜像专项手册

### 开发联调

1. [本机联调环境](local-management-stack.md)
2. [前端开发说明](../frontend/README.md)
3. [数据库迁移说明](../database/core-assets/README.md)
4. [开放 API](open-api-access.md)、[MCP](mcp-service.md)和[自动化库](../automation/README.md)

## 文档维护规则

- 通用部署流程只写在[部署指引](deployment-guide.md)；不要在公开文档记录站点 IP、资产数量、内部主机名或历史离线包日期。
- 联合模式统一使用 `deploy/combined` 的一个 Nginx 和一个 `443` 入口，同时保留管理/运维后端网络、数据与凭据隔离；独立模式仍使用各自入口。
- 新增环境变量、端口、服务或持久目录时，同时更新系统架构、部署指引和相邻组件 README。
- 验证报告必须注明范围和时间语义；后续功能实现不能反向改写历史测试结论。
- 示例不得包含真实密码、Token、私钥或可复用的生产 DSN。
