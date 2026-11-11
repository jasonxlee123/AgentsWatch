# 核心资产 PostgreSQL 本机验证报告

> 文档状态：2026-07-12 阶段性验证快照，只说明该次临时数据库测试结果；生产 Schema 以有序迁移为准。

## 1. 结论

核心资产逻辑架构已经在本机 PostgreSQL 16 原型中完成实现级验证。DDL、数据库约束、历史迁移、不可变记录和并发活动部署测试全部通过。

本结论只适用于本机临时数据库，不代表生产环境已经部署或验收。

验证日期：2026-07-12。

## 2. 本机环境与隔离

| 项目 | 配置 |
| --- | --- |
| 容器运行时 | Docker 29.4.3 |
| 数据库镜像 | `postgres:16-alpine` |
| 数据库 | `agentswatch_test` |
| 网络 | `--network none`，无宿主机端口 |
| 存储 | 无挂载卷，容器删除即清理 |
| 测试数据 | 全部为模拟 UUID 和模拟资产 |

测试没有连接生产数据库，没有修改监控 Docker Compose，也没有读取或写入生产清单。

## 3. 原型规模

DDL 创建四个 Schema：

- `org`：部门和人员。
- `asset`：环境、云、服务器、Runtime、Agent、部署、分组、快照和外部标识。
- `audit`：不可变 ChangeLog。
- `ingest`：ImportBatch、ImportRow、ImportIssue 和 ImportChange。

共创建 22 张表，并包含：

- UUID 主键和 `ON DELETE RESTRICT` 外键。
- 22 类受控枚举。
- 正数、端口、时间区间和条件字段 CHECK。
- 当前关系和活动部署的部分唯一索引。
- Deployment/Member Runtime 一致性的复合外键。
- 自动递增 row_version 的更新触发器。
- Snapshot 和 ChangeLog 的 UPDATE/DELETE 阻断触发器。

## 4. 已通过场景

| 场景 | 数据库机制 | 结果 |
| --- | --- | --- |
| 一个 Runtime 承载三个不同类型 Primary Agent | 不对 Deployment.runtime_id 建唯一约束 | 通过 |
| 主智能体允许多个可访问用户 | 当前 `(agent_id, person_id)` 唯一 | 通过 |
| Server/Primary Agent 多管理组 | 当前成员组合唯一 | 通过 |
| 云实例编码重复 | 部分唯一索引 | 阻断 |
| 同环境当前内网 IP 重复 | 部分唯一索引 | 阻断 |
| 容器 Runtime 缺镜像或容器名 | CHECK | 阻断 |
| Personal Agent 缺主要服务人员 | CHECK | 阻断 |
| testing/active Primary 缺管理员 | CHECK | 阻断 |
| 同一 Primary 第二个活动 Deployment | 部分唯一索引 | 阻断 |
| Member.runtime_id 与 Deployment 不一致 | 复合外键 | 阻断 |
| verified Member 缺 runtime_agent_key | CHECK | 阻断 |
| Deployment 出现第二个当前 Primary Member | 部分唯一索引 | 阻断 |
| Runtime 内当前 runtime_agent_key 重复 | 部分唯一索引 | 阻断 |
| ExternalIdentifier 同时指向两个对象 | `num_nonnulls(...)=1` | 阻断 |
| 当前外部标识重复 | 部分唯一索引 | 阻断 |
| Server 更新 | row_version 由 1 自动变为 2 | 通过 |
| 修改 RuntimeDiscoverySnapshot | 不可变触发器 | 阻断 |
| 删除 ChangeLog | 不可变触发器 | 阻断 |
| 删除仍被 Runtime 引用的 Server | RESTRICT 外键 | 阻断 |
| Excel 暂存批次、行、问题和差异 | FK 与唯一约束 | 通过 |
| 主 Agent 迁移 Runtime | 旧部署/成员结束，新部署/成员创建 | 通过，agent_id 不变 |

## 5. 并发验证

测试启动两个独立 PostgreSQL 会话，同时为同一个 Primary Agent 创建不同 Runtime 上的活动 Deployment：

1. 第一个事务插入后延迟提交。
2. 第二个事务插入相同 Primary Agent 的另一活动 Deployment。
3. PostgreSQL 唯一索引等待第一个事务完成。
4. 最终恰好一个事务提交，另一个因 `uq_agent_one_active_deployment` 失败。
5. 查询确认数据库中只有一条活动 Deployment。

因此，该约束不仅在顺序插入时有效，在本机真实并发事务下也有效。

## 6. 验证中修正的问题

首版 DDL 的容器字段和 verified key CHECK 使用了字符串比较。PostgreSQL CHECK 遇到 NULL 时可能得到 UNKNOWN 并允许写入，因此已增加显式 `IS NOT NULL` 条件，并用反例测试锁定。

这进一步确认：涉及条件必填的规则必须测试 NULL、空字符串和合法值三种情况，不能只检查空字符串。

## 7. 运行方式

```bash
./scripts/test_core_asset_postgres.sh
```

相关文件：

- [`database/core-assets/001_core_asset_schema.sql`](../database/core-assets/001_core_asset_schema.sql)
- [`tests/sql/core_asset_schema_test.sql`](../tests/sql/core_asset_schema_test.sql)
- [`scripts/test_core_asset_postgres.sh`](../scripts/test_core_asset_postgres.sh)

## 8. 尚未验证

- 生产服务器上的 PostgreSQL 版本、参数、磁盘和高可用方式。
- 生产数据库账号、最小权限、TLS、备份和恢复。
- 真实 Excel 文件解析、批次锁定和整批回滚。
- 真实部门编码、人员工号和 Runtime 发现结果的消歧。
- 低千级数据量下的查询计划和 API 性能。
- 未来领域服务负责的跨行生命周期和业务规则。

数据接入与发现预览已完成[本机原型验证](core-data-ingest-local-validation.md)。下一阶段应开发本机最小只读管理 API，不直接开放写入和批量操作。
