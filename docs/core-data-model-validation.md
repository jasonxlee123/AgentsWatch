# 核心资产数据模型模拟验证

> 文档状态：阶段性模拟验证快照。后续 PostgreSQL 迁移和领域 API 已实现，现行模型请阅读[核心资产数据架构](core-data-architecture.md)。

## 1. 目的与边界

本报告使用纯 JSON 模拟数据和无数据库校验器，验证《核心资产数据架构》中最关键的对象关系与条件约束。验证不会创建数据库、修改生产清单或实现导入服务。

本轮回答以下问题：

- 一台服务器能否承载多个 Runtime。
- 一个 Runtime 能否同时承载不同部门、不同服务类型的主智能体。
- 一个主智能体能否拥有多个历史部署，但最多只有一个活动部署。
- 子智能体能否稳定归属于主智能体并跟随部署。
- 服务器和主智能体能否同时属于多个不同用途的虚拟管理组。
- 个人、部门、业务三种服务类型能否保持唯一、互斥。
- 裸金属和容器两种 Runtime 部署模式能否使用同一抽象。

本轮同时对测试清单执行只读迁移演练，验证数量守恒、身份生成和待处理项。仍不验证 PostgreSQL DDL 的实际执行、Excel 差异提交、审计权限和真实 Runtime 发现。

## 2. 验证资产

- 模拟数据：[`tests/fixtures/core_assets_valid.json`](../tests/fixtures/core_assets_valid.json)
- 校验器：[`scripts/validate_core_asset_model.py`](../scripts/validate_core_asset_model.py)
- 自动化测试：[`tests/test_core_asset_model.py`](../tests/test_core_asset_model.py)
- 清单迁移演练器：[`scripts/dry_run_core_asset_migration.py`](../scripts/dry_run_core_asset_migration.py)
- 迁移演练测试：[`tests/test_core_asset_migration_dry_run.py`](../tests/test_core_asset_migration_dry_run.py)
- 导入契约模拟器：[`scripts/simulate_asset_import_contract.py`](../scripts/simulate_asset_import_contract.py)
- 导入契约测试：[`tests/test_asset_import_contract_simulation.py`](../tests/test_asset_import_contract_simulation.py)

校验器仅使用 Python 标准库，可独立运行：

```bash
python3 scripts/validate_core_asset_model.py
python3 -m unittest \
  tests.test_core_asset_model \
  tests.test_core_asset_migration_dry_run \
  tests.test_asset_import_contract_simulation -v
```

## 3. 模拟数据构成

| 对象 | 数量 | 设计意图 |
| --- | ---: | --- |
| Environment | 1 | 单环境基线 |
| CloudProvider | 2 | 华为云与私有云 |
| Department | 3 | 平台、财务、法律三个归属部门 |
| Person | 6 | 管理员、服务对象和多个可访问用户 |
| RuntimeFamily | 2 | OpenClaw 与 Hermes |
| ServerAsset | 3 | 裸金属宿主机、容器宿主机、未部署规划服务器 |
| ManagementGroup | 4 | 两个服务器组、两个智能体组 |
| RuntimeInstance | 3 | 共享 OpenClaw、容器 Hermes、空闲规划 Runtime |
| AgentAsset | 7 | 4 个主智能体、3 个子智能体 |
| AgentDeployment | 4 | 4 个主智能体的活动部署 |
| AgentDeploymentMember | 7 | 4 个主成员和 3 个子成员 |
| AgentAccessiblePerson | 4 | 验证一只主智能体允许多人访问 |

共享 OpenClaw Runtime 上同时部署了三个主智能体：

| 主智能体 | 归属部门 | 服务类型 | 子智能体 |
| --- | --- | --- | --- |
| 员工个人数字助手 | 平台运维部 | `personal` | 个人信息检索子智能体 |
| 财务部综合助手 | 示例财务部 | `department` | 财务报表子智能体 |
| 税务合规智能体 | 示例法务部 | `business` | 税务政策子智能体 |

这组数据专门验证 Runtime 不拥有部门或服务类型属性，也不以这些属性限制承载关系。

## 4. 验证场景与结果

| 场景 | 预期 | 结果 |
| --- | --- | --- |
| 同一 Runtime 承载第二个活动主智能体 | 拒绝 | `ACTIVE_RUNTIME_DEPLOYMENT_CONFLICT` |
| 一台服务器加入两个服务器管理组 | 允许 | 通过 |
| 一个主智能体加入两个智能体管理组 | 允许 | 通过 |
| 一个主智能体出现第二个活动部署 | 拒绝 | `ACTIVE_AGENT_DEPLOYMENT_CONFLICT` |
| 子智能体直接加入管理组 | 拒绝 | `SUBAGENT_GROUP_MEMBERSHIP` |
| 子智能体服务类型与主智能体不一致 | 拒绝 | `SUBAGENT_ARCHETYPE_MISMATCH` |
| 容器 Runtime 缺少镜像 | 拒绝 | `RUNTIME_MODE_FIELDS_INVALID` |
| 同一活动 Runtime 出现重复 runtime_agent_key | 拒绝 | `RUNTIME_AGENT_KEY_DUPLICATE` |
| 活动主智能体缺少业务管理员 | 拒绝 | `ACTIVE_AGENT_ADMIN_REQUIRED` |
| 服务器容量为零 | 拒绝 | `SERVER_CAPACITY_INVALID` |
| 生命周期使用未定义状态 | 拒绝 | `ENUM_VALUE_INVALID` |
| row_version 小于 1 | 拒绝 | `ROW_VERSION_INVALID` |
| 活动 Runtime 指向 planned Server | 拒绝 | `ACTIVE_RUNTIME_SERVER_STATE_INVALID` |
| 活动 Deployment 指向 retired Runtime | 拒绝 | `ACTIVE_DEPLOYMENT_RUNTIME_STATE_INVALID` |
| Member.runtime_id 与 Deployment 不一致 | 拒绝 | `DEPLOYMENT_MEMBER_RUNTIME_MISMATCH` |
| Deployment 缺少主成员 | 拒绝 | `DEPLOYMENT_PRIMARY_MEMBER_INVALID` |
| 主智能体迁移到另一服务器/容器 Runtime | 结束旧部署并新建部署 | 通过，agent_id 不变 |
| Runtime 销毁重建 | 迁移其唯一活动主智能体 | 通过，agent_id 不变 |
| 主智能体退役 | 结束活动部署并保留身份 | 通过，无物理删除 |
| 测试清单确定性拆分 | 数量守恒且 ID 不冲突 | 通过 |
| 聚合 Agent 数量但无明细 | 不创建虚假 Subagent | 通过，产生待处理项 |
| 新增对象与现有对象同名 | 不按名称匹配 | 通过，识别为 new |
| 同批重复对象 | 拒绝 | `DUPLICATE_IMPORT_ROW` |
| 改名与 IP 变化 | 逐字段生成 set | 通过 |
| Excel 空单元格 | 不修改 | 通过，生成 ignore |
| `__CLEAR__` 清空可空字段 | 生成明确 clear | 通过 |
| `__CLEAR__` 清空必填字段 | 拒绝 | `FIELD_NOT_CLEARABLE` |
| 暂存后 row_version 变化 | 整批不可提交 | `ROW_VERSION_CONFLICT` |
| Excel 显式退役 | 生命周期 set，不删除 | 通过 |

有效样例的汇总结果：

```json
{
  "ok": true,
  "errors": 0,
  "warnings": 0,
  "shared_runtime_count": 0,
  "max_primary_agents_per_runtime": 1,
  "servers_in_multiple_groups": 1,
  "agents_in_multiple_groups": 1
}
```

## 5. 本轮结论

当前关系模型已收紧为活动 Runtime 与主智能体一对一，不需要增加额外中间层。关键约束位置如下：

- `RuntimeInstance` 只描述技术运行环境，不记录业务部门和智能体服务类型。
- `AgentAsset` 独立记录唯一的 `service_archetype`、归属部门、管理员和服务对象。
- `AgentDeployment` 表达主智能体当前或历史上部署到哪个 Runtime。
- 主智能体最多一个活动 `AgentDeployment`；Runtime 同样最多一个活动 Deployment。
- `AgentDeploymentMember` 保存主智能体和子智能体在该 Runtime 内的实际 key，并保证活动 key 在 Runtime 内唯一。
- `ManagementGroup` 是纯虚拟管理标签，资源可多组归属，不改变部署或生命周期。

验证过程中落实了两个关键约束：

- `AgentDeployment` 同时对活动 `primary_agent_id` 和活动 `runtime_id` 建立唯一约束，页面、领域服务和数据库三层阻止重复占用。
- `AgentDeploymentMember` 原来没有 runtime_id，数据库无法直接建立 Runtime 内 key 唯一索引；现已增加冗余 runtime_id，并要求通过复合外键与 Deployment 保持一致。

只读迁移演练证明来源记录可以守恒拆分为 Server、Runtime、Primary Agent、Deployment 和 Snapshot；待处理项会明确报告，因此旧清单在问题解决前不可提交。

## 6. 尚未被本轮证明的规则

32 个数据架构专项测试已通过；主要 PostgreSQL 约束及双会话并发也已完成[本机实现级验证](core-data-postgresql-validation.md)。以下规则仍需通过真实 Excel 导入、领域服务或生产环境验证：

- 退役前置条件和历史区间的跨行领域服务编排。
- Excel 工作簿解析、数据库锁、row_version 二次核对和整批事务回滚。
- 发现快照内容哈希去重、JSONB 脱敏和 Agent 投影。
- 管理员姓名、部门名称和 Runtime Agent key 接入真实权威数据后的消歧结果。

因此，逻辑数据架构和本机 PostgreSQL 原型均已完成；下一阶段进入数据接入与发现。生产部署与真实导入验收前，仍不能宣称生产 Schema 已验收。
