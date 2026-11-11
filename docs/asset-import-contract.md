# 资产 Excel 导入契约

> 管理台当前采用精简的 `bulk-import-v1`：一个 `.xlsx` 只维护 `部门`、`人员`、`服务器`、`运行时`、`智能体` 五个工作表，按稳定编码匹配，上传后校验预览，确认后在一个事务内整批提交。模板以管理台“数据导入 → 下载 Excel 模板”为准。
>
> 本文后续的 13 Sheet `2.0` 契约仅供受控的初始迁移、导入规划和底层验证脚本使用，不是管理台产品流程，也不应扩展成数据问题治理模块。

## 1. 目的与边界

本文定义 Server、Runtime、Agent、虚拟管理分组和组织数据通过 Excel 长期维护时的文件格式、匹配、校验、冲突和事务提交规则。

Excel 是批量变更入口，不是数据库替代品。上传内容必须先进入暂存区，生成对象和字段级差异，确认后才可以提交。

禁止通过 Excel 导入：

- 密钥、Token、密码、私钥、Cookie。
- Runtime 发现快照、遥测、告警和日志。
- OpenClaw/Hermes 完整配置正文和状态备份。
- 临时 subagent run、会话或执行任务。

配套文档：

- [核心资产数据架构](core-data-architecture.md)
- [核心资产数据字典](core-data-dictionary.md)

## 2. 工作簿规范

- 只接受 `.xlsx`，不接受宏、外部链接、加密工作簿和 `.xls`。
- 工作簿必须包含 `_Meta` Sheet，其中 `agentswatch_template_version` 本版为 `2.0`。可同时写入同名自定义属性，但 `_Meta` 是导入服务的权威读取位置。
- 第一行是固定英文列名，第二行开始是数据。
- 关键标识字段不接受公式。
- 文本去除首尾空白；外部编码做 Unicode NFC 规范化。
- 日期使用带时区 ISO 8601。
- 布尔值只接受 `true`/`false`。
- UUID 使用小写标准连字符格式。

### 2.1 Sheet 与依赖顺序

| 顺序 | Sheet | 用途 |
| --- | --- | --- |
| 1 | `Departments` | 部门层级 |
| 2 | `People` | 人员、工号和所属部门 |
| 3 | `CloudProviders` | 可维护云厂商字典 |
| 4 | `Servers` | 服务器资产和简单当前配置 |
| 5 | `ManagementGroups` | 服务器/智能体虚拟管理分组 |
| 6 | `ServerGroupMembers` | 服务器分组成员 |
| 7 | `Agents` | 主/子 Agent 和服务类型 |
| 8 | `AgentAccessUsers` | Agent 可访问用户 |
| 9 | `AgentGroupMembers` | 主 Agent 分组成员 |
| 10 | `Runtimes` | OpenClaw/Hermes Runtime/Gateway |
| 11 | `AgentDeployments` | 主 Agent 到 Runtime 的部署关系 |
| 12 | `AgentDeploymentMembers` | 部署内主/子 Agent 映射 |
| 13 | `NetworkRequirements` | Agent 网络需求 |

Environment 和 RuntimeFamily 由平台预配置，本合同不通过 Excel 创建。

## 3. 通用导入语义

### 3.1 平台 ID 与版本

可变对象和关系都携带：

| 列 | 新增 | 更新 | 说明 |
| --- | --- | --- | --- |
| `platform_id` | 可空 | 必填 | 平台 UUID；新增由平台生成 |
| `row_version` | 可空 | 必填 | 更新目标当前版本 |

标准长期导入中，`platform_id` 为空表示新增。若其外部编码已属于现有对象，报 `PLATFORM_ID_REQUIRED_FOR_UPDATE`，不能自动变成更新。

只有受控的 `import_mode=initial_migration` 才允许在平台 ID 为空时按稳定外部编码匹配已有对象。import_mode 由导入服务设置，不从工作簿读取。

### 3.2 空值与清空

- 空单元格表示“不修改”；新增时表示未提供。
- `__CLEAR__` 表示主动清空可空字段。
- 非空字段使用 `__CLEAR__` 报错。
- 文件缺行不表示删除、停用或退役。
- 退役必须显式修改 `lifecycle_status`。

### 3.3 匹配规则

1. 更新行只按 `platform_id` 匹配。
2. 首次迁移可以按各对象规定的外部编码匹配。
3. 引用其他对象时，优先平台 ID；允许使用明确的稳定编码作为备选。
4. 名称、IP、账号、人员姓名和部门路径永远不自动匹配对象。
5. UUID 和外部编码同时存在时必须指向同一对象。

### 3.4 初始迁移的身份冻结

受控的初始迁移工作簿可以使用一次性确定性种子生成首次提交 UUID。Agent 种子中的 Runtime 槽位只用于本批初始建档，不是长期业务标识：

- 首次提交成功后，数据库中的平台 `agent_id` 成为唯一权威身份。
- 后续导入只允许使用平台 UUID，或已经存在、仍生效且不是本次冻结种子的 ExternalIdentifier 匹配。
- `runtime_seed_key` / `agent_seed_key` 仅允许用于完全相同初始计划的幂等重试，不能成为后续导入匹配键。
- 禁止因 Agent 名称、IP、Runtime 槽位或重新计算工作簿种子而改绑、合并或重建 Agent 身份。
- `source_fingerprint` 必须覆盖 `observed_at`、策略版本、`core-assets-015` Schema 契约版本、连接模式以及全部规范化持久化语义；任一语义变化都产生新计划身份。
- RuntimeDiscoverySnapshot 的 `content_hash` 覆盖除自身 ID/hash 外的全部计划持久化字段，包括原始载荷，不能以版本和数量等局部摘要代替。
- 完全相同计划在批次已提交后再次执行时，只校验不可变批次收据并返回幂等成功，不再拿初始计划覆盖或比较当前资产。连接健康、Runtime 管理驱动等提交后产生的正常运维变化必须保留；若要重新对账，应创建新的导入计划和批次。

初始计划默认 `connection_mode=none`。如果迁移需要 `ops-ssh`，连接目标、工作账号和凭据引用必须由经过审核的迁移计划提供；不得在工作簿、文档或日志中写入站点地址、密码或可复用凭据。`local-direct` 使用 `127.0.0.1`，仅保留给同进程开发验证，不用于迁移提交或容器化运行。

## 4. Sheet 字段

### 4.1 `Departments`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 通用字段 |
| `department_code` | 是 | 稳定且唯一 |
| `parent_department_code` | 否 | 必须存在且不得形成环 |
| `name` | 是 | 不参与匹配 |
| `is_active` | 是 | 布尔值 |

### 4.2 `People`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 通用字段 |
| `employee_no` | 是 | 唯一，按字符串保留前导零 |
| `display_name` | 是 | 允许重名 |
| `username` | 否 | 旧模板兼容列，仅留在导入原始载荷；不创建或匹配登录账号 |
| `primary_department_code` | 否 | 引用 Department |
| `employment_status` | 是 | `active`、`inactive` 或 `departed` |

### 4.3 `CloudProviders`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 通用字段 |
| `provider_code` | 是 | 唯一，例如 `huawei_cloud` |
| `name` | 是 | 例如华为云、私有云 |
| `provider_kind` | 是 | `public_cloud`, `private_cloud`, `other` |
| `status` | 是 | `active`, `disabled`, `retired` |

### 4.4 `Servers`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 通用字段 |
| `environment_code` | 是 | 引用预配置 Environment |
| `provider_code` | 否 | 引用 CloudProvider |
| `provider_instance_id` | 否 | 同一 Provider 内唯一 |
| `name` | 是 | 不参与匹配 |
| `lifecycle_status` | 是 | Server 生命周期枚举 |
| `cpu_cores` | 否 | 正整数 |
| `memory_gib` | 否 | 正数 |
| `disk_gib` | 否 | 正数，当前总容量 |
| `internal_ip` | 否 | 有效 IP；`__CLEAR__` 可清空 |
| `public_ip` | 否 | 有效 IP；`__CLEAR__` 可清空 |
| `os_name`, `os_version`, `architecture` | 否 | 当前配置 |
| `work_account` | 否 | 只允许用户名，不得包含凭据 |

Server 配置更新不创建新 Server，只递增 row_version 并写 ChangeLog。Server 退役前必须结束全部活动 Runtime 和服务器分组关系。

### 4.5 `ManagementGroups`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 通用字段 |
| `group_code` | 是 | 唯一 |
| `name` | 是 | 展示名 |
| `resource_type` | 是 | `server` 或 `agent` |
| `purpose`, `description` | 否 | 虚拟管理用途 |
| `lifecycle_status` | 是 | `active`, `disabled`, `retired` |

分组退役不改变成员资源状态。

### 4.6 `ServerGroupMembers`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 成员关系 UUID/版本 |
| `group_code` | 是 | 必须引用 server 类型 Group |
| `server_platform_id` | 推荐 | Server UUID |
| `provider_instance_id` | 条件必填 | Server UUID 为空时的引用候选 |
| `valid_from` | 是 | ISO 8601 |
| `valid_to` | 否 | 结束成员关系 |

同一 Server 可以加入多个分组；同一 Group/Server 活动关系只能一条。

### 4.7 `Agents`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 通用字段 |
| `agent_external_code` | 新增建议 | 可选稳定业务编码 |
| `name` | 是 | 允许重名，不参与匹配 |
| `structure_role` | 是 | `primary` 或 `subagent` |
| `parent_agent_platform_id` | Subagent 必填 | 必须引用 Primary |
| `service_archetype` | 是 | `personal`, `department`, `business`，唯一互斥 |
| `lifecycle_status` | 是 | Agent 生命周期枚举 |
| `owning_department_code` | 否 | 归属部门 |
| `business_admin_employee_no` | Testing/Active Primary 必填 | 业务管理员，仅一人 |
| `it_admin_employee_no` | Testing/Active Primary 必填 | IT 管理员，仅一人 |
| `primary_service_employee_no` | Personal Primary 必填 | 主要服务人员 |
| `primary_service_department_code` | Department Primary 必填 | 主服务部门 |
| `business_domain` | Business Primary 必填 | 业务领域 |
| `business_scenario` | Business Primary 必填 | 具体业务场景 |
| `description` | 否 | 说明 |

Subagent 的 service_archetype 必须与父 Agent 一致，服务人员、服务部门、业务领域和业务场景字段必须为空并继承父 Agent。第一阶段不允许 Subagent 再挂 Subagent。

### 4.8 `AgentAccessUsers`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 访问关系 UUID/版本 |
| `agent_platform_id` | 推荐 | Primary Agent UUID |
| `agent_external_code` | 条件必填 | Agent UUID 为空时使用 |
| `employee_no` | 是 | 引用 Person |
| `valid_from` | 是 | 生效时间 |
| `valid_to` | 否 | 结束时间 |

本 Sheet 只记录主 Agent 可访问用户，不实现实际授权。Subagent 不能单独配置访问用户；业务和 IT 管理员不要求重复出现在本表。

### 4.9 `AgentGroupMembers`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 成员关系 UUID/版本 |
| `group_code` | 是 | 必须引用 agent 类型 Group |
| `agent_platform_id` | 推荐 | 必须引用 Primary Agent |
| `agent_external_code` | 条件必填 | UUID 为空时使用 |
| `valid_from` | 是 | 生效时间 |
| `valid_to` | 否 | 结束时间 |

Subagent 不能直接加入 ManagementGroup。

### 4.10 `Runtimes`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 通用字段 |
| `legacy_runtime_id` | 否 | 首次迁移旧 Gateway ID |
| `server_platform_id` | 推荐 | Server UUID |
| `provider_instance_id` | 条件必填 | Server UUID 为空时引用 Server |
| `runtime_family_code` | 是 | 预配置 `openclaw`, `hermes` 等 |
| `name` | 是 | 展示名 |
| `runtime_role` | 是 | 第一版固定 `gateway` |
| `deployment_mode` | 是 | `baremetal` 或 `container` |
| `desired_version` | 否 | 计划版本 |
| `lifecycle_status` | 是 | Runtime 生命周期枚举 |
| `bind_address`, `endpoint_port` | 否 | 端点信息 |
| `runtime_account`, `config_path`, `state_path` | 否 | 不得填写凭据或配置正文 |
| `container_name`, `image_ref` | Container 必填 | Baremetal 必须为空 |

跨 Server 或部署模式迁移必须创建新 Runtime，不能修改已有 Runtime.server_id/deployment_mode 伪造迁移。

### 4.11 `AgentDeployments`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 部署 UUID/版本 |
| `primary_agent_platform_id` | 推荐 | 必须引用 Primary Agent |
| `agent_external_code` | 条件必填 | UUID 为空时使用 |
| `runtime_platform_id` | 推荐 | Runtime UUID |
| `legacy_runtime_id` | 条件必填 | Runtime UUID 为空时使用 |
| `lifecycle_status` | 是 | `planned`, `active`, `inactive`, `retired` |
| `valid_from` | 是 | 开始时间 |
| `valid_to` | 否 | 结束时间 |

一个 Primary Agent 在主数据加本批次结果中最多一个活动 Deployment；一个 Runtime 同样最多承载一个活动 Deployment。

### 4.12 `AgentDeploymentMembers`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 成员 UUID/版本 |
| `deployment_platform_id` | 是 | 引用 AgentDeployment |
| `runtime_platform_id` | 是 | 必须与 Deployment.runtime_id 一致，不允许独立改写 |
| `agent_platform_id` | 是 | 引用该主 Agent 或其直接 Subagent |
| `member_role` | 是 | `primary` 或 `subagent` |
| `parent_member_platform_id` | Subagent 必填 | 指向本 Deployment 主成员 |
| `runtime_agent_key` | 验证后必填 | Runtime 内部 Agent 键 |
| `mapping_status` | 是 | `planned`, `verified`, `unresolved` |
| `config_ref` | 否 | 只保存路径或引用 |
| `valid_from` | 是 | 生效时间 |
| `valid_to` | 否 | 结束时间 |

每个 Deployment 恰好一个主成员。同一 Runtime 的活动 runtime_agent_key 唯一；`unresolved` 允许 key 暂空，但必须进入数据质量问题列表。导出时携带 `runtime_platform_id`，导入服务以 Deployment 为权威值复核，二者不一致时报 `DEPLOYMENT_MEMBER_RUNTIME_MISMATCH`。

### 4.13 `NetworkRequirements`

| 列 | 必填 | 规则 |
| --- | --- | --- |
| `platform_id`, `row_version` | 更新必填 | 关系 UUID/版本 |
| `agent_platform_id` | 推荐 | Agent UUID |
| `agent_external_code` | 条件必填 | UUID 为空时使用 |
| `system_name` | 是 | 目标系统 |
| `target_type`, `target_value` | 是 | DNS/IP/CIDR/URL 及值 |
| `protocol` | 是 | TCP/UDP/HTTP/HTTPS |
| `port_from`, `port_to` | 条件必填 | 端口范围 |
| `purpose` | 否 | 用途 |
| `valid_from` | 是 | 生效时间 |
| `valid_to` | 否 | 结束时间 |

## 5. 校验、差异和提交

### 5.1 必须校验

- 模板版本、必需 Sheet/列和数据类型。
- 平台 ID、外部标识、引用对象和 row_version。
- Server 配置正数、IP 和条件字段。
- ManagementGroup 类型与成员类型一致。
- Agent 主从层级、唯一服务类型和条件必填字段。
- 管理员、可访问用户、部门和人员编码存在。
- Primary Agent 活动 Deployment 唯一。
- Deployment 主成员唯一，子成员属于该主 Agent。
- Runtime 裸金属/容器条件字段。
- 单元格是否疑似包含 Secret。

### 5.2 稳定错误码

| 错误码 | 含义 |
| --- | --- |
| `PLATFORM_ID_REQUIRED_FOR_UPDATE` | 长期导入试图仅凭外部编码更新对象 |
| `ROW_VERSION_CONFLICT` | 暂存后主数据已变化 |
| `IDENTIFIER_TARGET_MISMATCH` | UUID 和外部编码指向不同对象 |
| `REFERENCE_NOT_FOUND` | 引用对象不存在 |
| `INVALID_SERVICE_ARCHETYPE_FIELDS` | Agent 服务类型条件字段不合法 |
| `INVALID_AGENT_HIERARCHY` | 主从层级非法或形成多层/循环 |
| `GROUP_RESOURCE_TYPE_MISMATCH` | 分组和成员类型不一致 |
| `SUBAGENT_GROUP_MEMBERSHIP` | Subagent 被直接加入分组 |
| `ACTIVE_AGENT_DEPLOYMENT_CONFLICT` | 主 Agent 将出现多个活动部署 |
| `ACTIVE_RUNTIME_DEPLOYMENT_CONFLICT` | Runtime 将出现多个活动主智能体部署 |
| `DEPLOYMENT_PRIMARY_MEMBER_INVALID` | 部署缺少或包含多个主成员 |
| `DEPLOYMENT_MEMBER_RUNTIME_MISMATCH` | 成员冗余 Runtime 与所属 Deployment 不一致 |
| `ACTIVE_RUNTIME_SERVER_STATE_INVALID` | 活动 Runtime 所在 Server 状态不允许承载 |
| `ACTIVE_DEPLOYMENT_RUNTIME_STATE_INVALID` | 活动 Deployment 指向非活动 Runtime |
| `RUNTIME_MODE_FIELDS_INVALID` | Runtime 部署模式条件字段不合法 |
| `FIELD_NOT_CLEARABLE` | 非空字段请求清空 |
| `SECRET_CONTENT_DETECTED` | 单元格疑似包含秘密 |
| `PLACEHOLDER_VALUE` | 检测到“定向用户”等占位文本 |

### 5.3 差异与并发

字段差异分为 `set`, `clear`, `ignore`, `conflict`。用户只能选择 set/clear；conflict 必须修正后重新校验。

上传时记录目标 row_version，提交前再次核对。任一对象版本不同则整个批次拒绝提交，不做自动三方合并。

### 5.4 事务提交

1. 锁定 ready 状态 ImportBatch。
2. 校验全部 row_version。
3. 按依赖顺序创建/更新对象和关系。
4. 重新执行跨表约束。
5. 写入不可变 ChangeLog。
6. 将批次标记为 committed。
7. 提交单个事务。

任一步失败均回滚，不允许部分成功。对 committed 批次重复提交返回原结果，不重复执行。

### 5.5 可执行语义验证

无数据库差异模拟器 [`scripts/simulate_asset_import_contract.py`](../scripts/simulate_asset_import_contract.py) 已验证本契约的字段级语义，测试位于 [`tests/test_asset_import_contract_simulation.py`](../tests/test_asset_import_contract_simulation.py)。覆盖结果如下：

- 新对象即使与现有对象同名也不会按名称匹配。
- 同批重复行被拒绝。
- 改名和 IP 变化生成独立 `set` 差异。
- 空单元格生成 `ignore`，不覆盖现值。
- `__CLEAR__` 只对允许为空的字段生成 `clear`。
- row_version 不一致生成 `ROW_VERSION_CONFLICT`。
- 退役必须是显式生命周期 `set`。
- 外部编码只在 `initial_migration` 模式允许匹配现有对象。

该模拟证明差异语义，不替代真实 Excel 解析、数据库锁和事务回滚测试。

真实 `.xlsx` 本机原型已经补充完成：[`scripts/preview_asset_import.py`](../scripts/preview_asset_import.py) 读取 `_Meta`、13 个数据 Sheet 和固定表头，生成 ImportBatch、ImportRow、ImportIssue、ImportChange 暂存载荷；[`scripts/test_core_data_ingest_local.sh`](../scripts/test_core_data_ingest_local.sh) 已验证暂存载荷能够写入隔离临时 PostgreSQL。该结果仍不包含用户确认和正式主数据提交。

## 6. 旧清单迁移

专用迁移适配器读取本地、未提交的清单，生成与 Excel 相同的暂存、问题和差异，不直接写主表。先在隔离环境预览，人工处理所有未解析项，再由获批人员确认提交；不要把生产清单或预览结果提交到仓库。

### 6.1 拆分结果

每条旧记录暂拆为：

- 1 个 ServerAsset。
- 1 个 OpenClaw baremetal RuntimeInstance。
- 1 个 Primary AgentAsset。
- 1 个活动 AgentDeployment。
- 1 个主 AgentDeploymentMember，mapping_status 初始为 `unresolved`，直到发现真实 runtime_agent_key。
- 1 个 RuntimeDiscoverySnapshot。
- 若干人员、部门和网络需求候选关系。

### 6.2 字段映射

| 旧字段 | 新模型 |
| --- | --- |
| `defaults.cluster/env/region/team` | Environment |
| `metadata.deploy_location` | 已审核的 CloudProvider |
| `metadata.server_id` | Server.provider_instance_id 和旧外部标识 |
| `host` | Server.internal_ip |
| `gateway_id` | Runtime legacy 外部标识 |
| `deployment_mode`, `port` | Runtime 部署模式和端点 |
| `openclaw_version` | RuntimeDiscoverySnapshot.actual_version |
| `lobster_name` | Primary Agent.name |
| `department` | Agent.owning_department_id 候选 |
| `it_owner`, `business_owner` | Agent 管理员候选，必须补人员工号 |
| `target_user`, `target_department` | 服务类型/服务对象/访问用户候选，必须人工确认 |
| `service_mode`, `mounted_agents` | Agent 结构候选和 Runtime 观测计数，不生成虚假 Subagent |
| `network_requirements` | AgentNetworkRequirement |
| 飞书应用、交互方式 | 仅保留迁移原始载荷 |

### 6.3 迁移结果核验

| 对象 | 核验要求 |
| --- | ---: |
| Environment、CloudProvider | 与获批迁移范围一致 |
| ServerAsset、RuntimeInstance、Primary AgentAsset | 与来源记录一一对应，或有明确的拆分说明 |
| Subagent AgentAsset | 只在来源具有可核验明细时创建 |
| 活动 AgentDeployment、主 AgentDeploymentMember、RuntimeDiscoverySnapshot | 与成功解析的 Runtime 一一对应 |
| ManagementGroup | 仅按获批台账创建，不从旧字段推断 |

来源中的聚合 Agent 数量只写入 `observed_agent_count`，可信度标记为 `legacy`，不作为创建子 Agent 的依据。

迁移前必须补齐或人工处理：

- 每个 Primary Agent 的唯一 service_archetype。
- Personal/Department/Business 对应的条件字段。
- 两名管理员的人员工号。
- 部门路径到部门编码的映射。
- `target_user=定向用户` 等占位值。
- Runtime 内主/子 Agent 的真实 key 和子 Agent 明细。

不能确认的数据必须进入迁移问题报告，不得静默推断。

## 7. 验收场景

| 场景 | 预期结果 |
| --- | --- |
| Server 配置调整 | 更新当前值、递增 row_version、写 ChangeLog |
| Server 加入多个组 | 创建多条 ServerGroupMember |
| 主 Agent 加入多个组 | 创建多条 AgentGroupMember |
| Subagent 加组 | 拒绝 |
| 第二个主 Agent 占用同一活动 Runtime | 整批拒绝，报告 `ACTIVE_RUNTIME_DEPLOYMENT_CONFLICT` |
| 主 Agent 第二个活动 Runtime | 拒绝 |
| Subagent 服务类型与父级不同 | 拒绝 |
| Business Agent 缺场景 | 拒绝 |
| Personal Agent 多个主要服务人员 | 拒绝；额外人员进入 AccessUsers |
| Runtime 从裸金属迁移到容器 | 创建新 Runtime 和新 Deployment |
| 聚合 Agent 数量但无明细 | 只保存观测计数，不生成 Subagent |
| Excel 空单元格 | 不修改现值 |
| 使用 `__CLEAR__` | 仅清空允许为空的字段 |
| 并发修改 | row_version 冲突，整批拒绝 |

## 8. 导出契约

- 使用同一模板和 Sheet 结构。
- 每行携带 platform_id 和 row_version。
- 名称列只用于核对，不参与重新匹配。
- 历史失效关系默认不导出。
- 不导出 Runtime 原始发现 JSON、配置摘要、Secret 或审计前后值。
- restricted 数据导出记录操作者、范围和文件哈希。
