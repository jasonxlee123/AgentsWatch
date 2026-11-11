# 核心资产数据字典

> 本字典对应逻辑架构基线 v1.0，已由[本机 PostgreSQL 16 原型](core-data-postgresql-validation.md)验证主要类型与约束；不代表生产 Schema 已部署。

## 1. 约定

本文是 [核心资产数据架构](core-data-architecture.md) 的 PostgreSQL 逻辑字段契约。表名使用 `schema.table`，字段使用 `snake_case`。本文只定义目标结构，不代表数据库已经创建。

### 1.1 类型约定

- 平台主键：`uuid`，由应用生成，不从业务字段推导。
- 时间：`timestamptz`，统一保存 UTC。
- 并发版本：`bigint`，初始为 `1`，每次更新 `+1`。
- IP：`inet`。
- 容量：GiB，使用 `numeric`，不使用含义不清的整数“G”。
- 原始扩展数据：`jsonb`。
- SHA-256：`char(64)` 十六进制小写。
- 外键默认 `ON DELETE RESTRICT`。

### 1.2 权威来源

| 值 | 含义 |
| --- | --- |
| `platform` | 平台生成或未来管理系统维护 |
| `import` | Excel 确认后写入的计划态 |
| `discovery` | Server/Runtime 只读发现产生的观测态 |
| `derived` | 根据其他数据计算，不接受人工编辑 |
| `migration` | 旧清单首次迁移 |

### 1.3 敏感级别

| 级别 | 含义 |
| --- | --- |
| `internal` | 普通内部资产数据 |
| `restricted` | IP、账号、人员和配置摘要等受限数据 |
| `secret` | 密钥、密码、Token、Cookie；只能加密保存，禁止明文、查询投影和审计输出 |

所有 JSONB 原始载荷入库前必须脱敏。除专用加密凭据字段外，检测到秘密时拒绝入库，不得把秘密降级为 restricted 数据。

### 1.4 公共可变字段

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `row_version` | `bigint` | 否 / `1` | `>=1`，更新递增 | platform | internal |
| `created_at` | `timestamptz` | 否 / `now()` | 创建后不可改 | platform | internal |
| `updated_at` | `timestamptz` | 否 / `now()` | 更新时刷新 | platform | internal |
| `retired_at` | `timestamptz` | 是 | 仅退役状态有值 | platform/import | internal |

## 2. 枚举

| 枚举 | 允许值 |
| --- | --- |
| `environment_lifecycle` | `planned`, `active`, `disabled`, `retired` |
| `cloud_provider_status` | `active`, `disabled`, `retired` |
| `server_lifecycle` | `planned`, `active`, `maintenance`, `disabled`, `retired` |
| `group_resource_type` | `server`, `runtime`, `agent` |
| `group_lifecycle` | `active`, `disabled`, `retired` |
| `runtime_role` | `gateway` |
| `deployment_mode` | `baremetal`, `container` |
| `runtime_lifecycle` | `planned`, `active`, `disabled`, `retired` |
| `runtime_observed_status` | `unknown`, `running`, `stopped`, `degraded`, `unreachable` |
| `runtime_management_driver` | `none`, `systemd`, `systemd_user`, `launchd`, `mock` |
| `agent_structure_role` | `primary`, `subagent` |
| `agent_service_archetype` | `personal`, `department`, `business` |
| `agent_lifecycle` | `requested`, `testing`, `active`, `suspended`, `retired` |
| `agent_channel_type` | `feishu`；无记录/停用表示不设置渠道 |
| `agent_deployment_lifecycle` | `planned`, `active`, `inactive`, `retired` |
| `deployment_member_mapping_status` | `planned`, `verified`, `unresolved` |
| `discovery_status` | `succeeded`, `partial`, `failed` |
| `network_target_type` | `dns`, `ipv4`, `ipv6`, `cidr`, `url` |
| `network_protocol` | `tcp`, `udp`, `http`, `https` |
| `import_mode` | `standard`, `initial_migration` |
| `import_status` | `uploaded`, `validating`, `ready`, `committing`, `committed`, `rejected`, `failed`, `superseded` |
| `import_row_status` | `new`, `matched`, `unchanged`, `changed`, `conflict`, `invalid`, `ignored`, `committed` |
| `change_operation` | `create`, `update`, `retire`, `restore`, `link`, `unlink` |

## 3. 组织与环境

### 3.1 `asset.environment`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `environment_id` | `uuid` | 否 | PK，不可变 | platform | internal |
| `environment_code` | `text` | 否 | UNIQUE | import/platform | internal |
| `name` | `text` | 否 | 非空 | import/platform | internal |
| `stage` | `text` | 否 | 建议 `prod`, `test`, `dev` | import/platform | internal |
| `region` | `text` | 是 | 展示和筛选 | import/platform | internal |
| `operations_team` | `text` | 是 | 平台统一运维团队 | import/platform | internal |
| `lifecycle_status` | `environment_lifecycle` | 否 / `active` | 受控状态转换 | platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

当前迁移创建 `environment_code=lobster-prod`、`stage=prod`、`operations_team=core`。

### 3.2 `org.department`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `department_id` | `uuid` | 否 | PK，不可变 | platform | internal |
| `department_code` | `text` | 否 | UNIQUE，稳定外部编码 | import | internal |
| `parent_department_id` | `uuid` | 是 | FK self，不得成环 | import | internal |
| `name` | `text` | 否 | 允许变化 | import | internal |
| `display_path` | 非持久化派生值 | — | 当前不落库；需要时由 `parent_department_id` 层级计算 | derived | internal |
| `is_active` | `boolean` | 否 / `true` | 停用不删除 | import | internal |
| `row_version`、时间字段 | 见 1.4 |  | 不使用 `retired_at` |  |  |

### 3.3 `org.person`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `person_id` | `uuid` | 否 | PK，不可变 | platform | restricted |
| `employee_no` | `text` | 否 | UNIQUE；保留前导零 | import | restricted |
| `display_name` | `text` | 否 | 允许重名和变化 | import | restricted |
| `username` | 非主数据字段 | — | 仅保留在旧导入原始载荷中；登录账号 `security.user_account` 与 Person 完全独立 | legacy | restricted |
| `primary_department_id` | `uuid` | 是 | FK Department | import | restricted |
| `employment_status` | `text` | 否 / `active` | `active`, `inactive`, `departed` | import/platform | restricted |
| `row_version`、时间字段 | 见 1.4 |  | 不使用 `retired_at` |  |  |

## 4. 云厂商与服务器

### 4.1 `asset.cloud_provider`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `cloud_provider_id` | `uuid` | 否 | PK | platform | internal |
| `provider_code` | `text` | 否 | UNIQUE，例如 `huawei_cloud` | import/platform | internal |
| `name` | `text` | 否 | 可配置展示名 | import/platform | internal |
| `provider_kind` | `text` | 否 | `public_cloud`, `private_cloud`, `other` | import/platform | internal |
| `status` | `cloud_provider_status` | 否 / `active` | 退役前检查 Server 引用 | platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

### 4.2 `asset.server_asset`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `server_id` | `uuid` | 否 | PK，不可变 | platform | internal |
| `environment_id` | `uuid` | 否 | FK Environment | import/platform | internal |
| `cloud_provider_id` | `uuid` | 是 | FK CloudProvider | import/platform | internal |
| `provider_instance_id` | `text` | 是 | 同一 CloudProvider 内活动值唯一 | import | restricted |
| `name` | `text` | 否 | 非空，不作匹配键 | import/platform | internal |
| `lifecycle_status` | `server_lifecycle` | 否 / `planned` | 受控状态转换 | import/platform | internal |
| `cpu_cores` | `integer` | 是 | `>0` | import/platform | internal |
| `memory_gib` | `numeric(10,2)` | 是 | `>0` | import/platform | internal |
| `disk_gib` | `numeric(12,2)` | 是 | `>0`，当前总容量 | import/platform | internal |
| `internal_ip` | `inet` | 是 | 同一 Environment 活动值唯一 | import/platform | restricted |
| `public_ip` | `inet` | 是 | 可因 NAT/共享出口重复，不作身份或匹配键 | import/platform | restricted |
| `os_name` | `text` | 是 | 例如 Ubuntu | import/platform | internal |
| `os_version` | `text` | 是 | 当前版本 | import/platform | internal |
| `architecture` | `text` | 是 | 例如 `x86_64`, `aarch64` | import/platform | internal |
| `work_account` | `text` | 是 | 只保存用户名 | import/platform | restricted |
| `config_updated_at` | `timestamptz` | 否 / `now()` | 任一配置字段修改时刷新 | platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

第一版不建立多磁盘、多网卡和多账号子表。配置修改更新当前值、递增 row_version，并把前后值写入 ChangeLog。资产表、查询投影和 ChangeLog 禁止保存密码、私钥或 sudo 凭据。服务器长期 SSH 登录密码是唯一受控例外：只允许以独立密钥加密后写入 `security.server_ssh_credential`，只读角色无权访问，且仅在显式首次/重新纳管时解密；日常 Ansible 任务固定使用平台公钥，不自动回退到密码。

## 5. 虚拟管理分组

### 5.1 `asset.management_group`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `group_id` | `uuid` | 否 | PK | platform | internal |
| `group_code` | `text` | 否 | UNIQUE | import/platform | internal |
| `name` | `text` | 否 | 非空 | import/platform | internal |
| `resource_type` | `group_resource_type` | 否 | Server 或 Agent，不得混装 | import/platform | internal |
| `purpose` | `text` | 是 | 虚拟分组用途说明 | import/platform | internal |
| `description` | `text` | 是 | 说明 | import/platform | internal |
| `lifecycle_status` | `group_lifecycle` | 否 / `active` | 退役不影响成员资产 | platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

### 5.2 `asset.server_group_member`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `server_group_member_id` | `uuid` | 否 | PK | platform | internal |
| `group_id` | `uuid` | 否 | FK ManagementGroup | import/platform | internal |
| `server_id` | `uuid` | 否 | FK ServerAsset | import/platform | internal |
| `valid_from` | `timestamptz` | 否 / `now()` | 生效时间 | platform | internal |
| `valid_to` | `timestamptz` | 是 | 晚于 valid_from | platform | internal |
| `row_version` | `bigint` | 否 / `1` | `>=1` | platform | internal |

活动 `(group_id, server_id)` 唯一。领域服务保证 group.resource_type=`server`。

### 5.3 `asset.agent_group_member`

字段与 ServerGroupMember 相同，将 `server_id` 替换为 `agent_id`。领域服务保证 group.resource_type=`agent` 且 Agent.structure_role=`primary`。

## 6. Runtime

### 6.1 `asset.runtime_family`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `runtime_family_id` | `uuid` | 否 | PK | platform | internal |
| `family_code` | `text` | 否 | UNIQUE，例如 `openclaw`, `hermes` | platform | internal |
| `name` | `text` | 否 | 展示名 | platform | internal |
| `status` | `text` | 否 / `active` | `active`, `disabled`, `retired` | platform | internal |
| `metadata` | `jsonb` | 否 / `{}` | 非秘密适配器元数据 | platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

### 6.2 `asset.runtime_instance`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `runtime_id` | `uuid` | 否 | PK，不可变 | platform | internal |
| `server_id` | `uuid` | 否 | FK ServerAsset | import/platform | internal |
| `runtime_family_id` | `uuid` | 否 | FK RuntimeFamily | import/platform | internal |
| `name` | `text` | 否 | 非空 | import/platform | internal |
| `runtime_role` | `runtime_role` | 否 / `gateway` | 第一版固定 gateway | platform | internal |
| `deployment_mode` | `deployment_mode` | 否 | 裸金属或容器 | import/platform | internal |
| `desired_version` | `text` | 是 | 计划态版本 | import/platform | internal |
| `lifecycle_status` | `runtime_lifecycle` | 否 / `planned` | 受控状态转换 | import/platform | internal |
| `observed_status` | `runtime_observed_status` | 否 / `unknown` | 不接受人工编辑 | discovery | internal |
| `bind_address` | `text` | 是 | 允许 `loopback`, `lan`, IP 等框架值 | import/platform | restricted |
| `endpoint_port` | `integer` | 是 | `1..65535` | import/platform | restricted |
| `runtime_account` | `text` | 是 | 裸金属运行账号或容器用户 | import/platform | restricted |
| `config_path` | `text` | 是 | 只保存路径，不存配置正文 | import/platform | restricted |
| `state_path` | `text` | 是 | 状态目录路径 | import/platform | restricted |
| `container_name` | `text` | 是 | 容器模式必填 | import/platform | internal |
| `image_ref` | `text` | 是 | 容器模式必填 | import/platform | internal |
| `management_driver` | `runtime_management_driver` | 否 / `none` | systemd/systemd_user/launchd/mock 仅允许裸金属 | platform | internal |
| `management_target` | `text` | 是 | systemd unit 或 launchd Label；driver 非 none 时必填 | platform | restricted |
| `command_path` | `text` | 是 | 可执行文件绝对路径；为空表示由管理驱动解析 | platform | restricted |
| `last_operation_at` | `timestamptz` | 是 | 最近标准任务执行时间 | platform | internal |
| `last_seen_at` | `timestamptz` | 是 | 最近观测时间 | discovery | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

条件约束：

- Baremetal：`container_name`、`image_ref` 为空。
- Container：`container_name`、`image_ref` 非空。
- `command_path` 为空时由登录用户的 PATH 发现；非空时必须是绝对路径，并优先于 PATH。
- Runtime 所属 Server 不能为 retired。

### 6.3 `asset.runtime_discovery_snapshot`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `snapshot_id` | `uuid` | 否 | PK，不可变 | platform | internal |
| `runtime_id` | `uuid` | 否 | FK RuntimeInstance | discovery | internal |
| `discovered_at` | `timestamptz` | 否 | 发现时间 | discovery | internal |
| `discovery_status` | `discovery_status` | 否 | 成功/部分/失败 | discovery | internal |
| `discovery_source` | `text` | 否 | `ssh`, `runtime_api`, `migration` 等 | discovery/migration | internal |
| `actual_version` | `text` | 是 | 实际版本 | discovery/migration | internal |
| `observed_agent_count` | `integer` | 是 | `>=0` | discovery/migration | internal |
| `agent_count_confidence` | `text` | 否 / `verified` | `verified`, `reported`, `legacy` | discovery/migration | internal |
| `content_hash` | `char(64)` | 否 | 脱敏规范化快照哈希 | discovery/migration | restricted |
| `extension_summary` | `jsonb` | 否 / `{}` | 插件/扩展摘要 | discovery | restricted |
| `config_summary` | `jsonb` | 否 / `{}` | 脱敏配置摘要 | discovery | restricted |
| `raw_payload` | `jsonb` | 否 | 脱敏原始发现结果 | discovery/migration | restricted |
| `error_code` | `text` | 是 | 失败码 | discovery | internal |
| `error_summary` | `text` | 是 | 不含秘密 | discovery | restricted |
| `created_at` | `timestamptz` | 否 / `now()` | 创建后只读 | platform | internal |

快照按相对最新内容变化新增，不更新旧快照；历史上重复出现相同内容仍保留新的变化事件。

### 6.4 `asset.runtime_web_endpoint`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `runtime_web_endpoint_id` | `uuid` | 否 | PK | platform | internal |
| `runtime_id` | `uuid` | 否 | FK RuntimeInstance；每个运行时最多一个启用的主入口 | platform | internal |
| `endpoint_code` | `text` | 否 / `control_ui` | Runtime 内唯一 | platform | internal |
| `display_name` | `text` | 否 / `WebUI` | 非空 | platform | internal |
| `access_mode` | `runtime_web_access_mode` | 否 | `local_ssh_tunnel`, `tailscale_serve`, `direct_url` | platform | restricted |
| `scheme` | `text` | 否 / `http` | `http`, `https` | platform | internal |
| `direct_url` | `text` | 是 | Serve/直连必填；只允许 HTTP(S) origin | platform | restricted |
| `remote_host` | `text` | 否 / `127.0.0.1` | SSH 通道只允许远端回环主机 | platform | restricted |
| `port_override` | `integer` | 是 | `1..65535`；为空继承 Runtime.endpoint_port | platform | restricted |
| `base_path` | `text` | 否 / `/` | 绝对路径且不含查询或 fragment | platform | internal |
| `auth_mode` | `runtime_web_auth_mode` | 否 / `none` | `none`, `token`, `password`, `username_password` | platform | restricted |
| `encrypted_credential` | `text` | 是 | auth_mode 非 none 时必填；查询和审计禁止返回 | platform | secret |
| `enabled`, `is_primary` | `boolean` | 否 / `true` | 部分唯一索引限制启用主入口 | platform | internal |
| `last_opened_at` | `timestamptz` | 是 | 最近成功建立访问的时间 | platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

### 6.5 `automation.runtime_web_access_session`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `access_session_id` | `uuid` | 否 | PK | platform | internal |
| `runtime_web_endpoint_id`, `runtime_id` | `uuid` | 否 | FK；禁止级联删除 | platform | internal |
| `requested_by` | `text` | 否 | 非空 | platform | restricted |
| `access_mode` | `runtime_web_access_mode` | 否 | 创建时的通道快照 | platform | restricted |
| `status` | `web_access_session_status` | 否 / `opening` | `opening`, `active`, `failed`, `closed`, `expired` | platform | internal |
| `local_port`, `process_id` | `integer` | 是 | SSH/本机通道运行信息 | platform | restricted |
| `failure_reason` | `text` | 是 | 不得包含凭据 | platform | restricted |
| `created_at`, `activated_at`, `expires_at`, `closed_at` | `timestamptz` | 按状态 | expires_at 晚于 created_at；关闭/过期必须有 closed_at | platform | internal |

## 7. Agent

### 7.1 `asset.agent_asset`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `agent_id` | `uuid` | 否 | PK，不可变 | platform | internal |
| `name` | `text` | 否 | 允许重名和改名 | import/platform | internal |
| `structure_role` | `agent_structure_role` | 否 | 主或子 Agent | import/platform | internal |
| `parent_agent_id` | `uuid` | 是 | FK self；Subagent 必填 | import/platform | internal |
| `service_archetype` | `agent_service_archetype` | 否 | 唯一、互斥 | import/platform | internal |
| `lifecycle_status` | `agent_lifecycle` | 否 / `requested` | 受控状态转换 | import/platform | internal |
| `owning_department_id` | `uuid` | 是 | FK Department | import/platform | internal |
| `business_admin_person_id` | `uuid` | 是 | FK Person；最多一人 | import/platform | restricted |
| `it_admin_person_id` | `uuid` | 是 | FK Person；最多一人 | import/platform | restricted |
| `primary_service_person_id` | `uuid` | 是 | Personal 主 Agent 必填 | import/platform | restricted |
| `primary_service_department_id` | `uuid` | 是 | Department 主 Agent 必填 | import/platform | internal |
| `business_domain` | `text` | 是 | Business 主 Agent 必填 | import/platform | internal |
| `business_scenario` | `text` | 是 | Business 主 Agent 必填 | import/platform | internal |
| `description` | `text` | 是 | 业务说明 | import/platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

领域约束：

- Primary：`parent_agent_id IS NULL`。
- Subagent：`parent_agent_id` 指向 Primary，不允许多层嵌套。
- Subagent 的 service_archetype 必须与父 Agent 相同；服务对象/场景字段为空并从父 Agent 继承。
- Personal Primary：只有 primary_service_person_id 非空。
- Department Primary：只有 primary_service_department_id 非空。
- Business Primary：business_domain、business_scenario 非空，两个 primary_service 字段为空。
- Primary 进入 testing/active 前两个管理员均非空；管理员可以是同一 Person。

### 7.2 `asset.agent_accessible_person`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `agent_accessible_person_id` | `uuid` | 否 | PK | platform | restricted |
| `agent_id` | `uuid` | 否 | FK AgentAsset | import/platform | restricted |
| `person_id` | `uuid` | 否 | FK Person | import/platform | restricted |
| `valid_from` | `timestamptz` | 否 / `now()` | 生效时间 | platform | internal |
| `valid_to` | `timestamptz` | 是 | 晚于 valid_from | platform | internal |
| `row_version` | `bigint` | 否 / `1` | `>=1` | platform | internal |

活动 `(agent_id, person_id)` 唯一，且 agent_id 必须指向 Primary Agent。本表只是主数据记录，不直接授予登录或调用权限。

### 7.3 `asset.agent_channel_configuration`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `agent_channel_configuration_id` | `uuid` | 否 | PK | platform | internal |
| `agent_id` | `uuid` | 否 | FK AgentAsset；全表唯一；Primary/Subagent 均允许 | platform | internal |
| `channel_type` | `agent_channel_type` | 否 | 当前仅 `feishu` | platform | internal |
| `application_name` | `text` | 否 | 非空；未填写时由服务默认成 Agent.name | platform | internal |
| `app_id` | `text` | 否 | 非空；启用状态下与 channel_type 组合唯一 | platform | restricted |
| `encrypted_app_secret` | `text` | 否 | 仅保存带认证的密文；禁止查询投影和审计输出 | platform | secret |
| `enabled` | `boolean` | 否 / `true` | false 表示不设置渠道并保留历史配置 | platform | internal |
| 公共可变字段 | 见 1.4 |  |  |  |  |

Primary 和 Subagent 分别维护自己的渠道配置，互不继承。创建飞书配置或修改 AppID 必须提供 AppSecret；只修改其他字段时空 AppSecret 表示保留现有密文。API 永不返回 `encrypted_app_secret` 或明文 AppSecret，ChangeLog 只记录脱敏后的 `credential_configured`。

## 8. Agent 部署

### 8.1 `asset.agent_deployment`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `agent_deployment_id` | `uuid` | 否 | PK | platform | internal |
| `primary_agent_id` | `uuid` | 否 | FK AgentAsset；必须为 Primary | import/platform | internal |
| `primary_agent_structure_role` | `agent_structure_role` | 否 / `primary` | 固定为 Primary；与 `primary_agent_id` 组成复合 FK，禁止引用 Subagent | platform | internal |
| `runtime_id` | `uuid` | 否 | FK RuntimeInstance；一个 Runtime 最多一个 active | import/platform | internal |
| `lifecycle_status` | `agent_deployment_lifecycle` | 否 / `planned` | 一个 Agent 最多一个 active | import/platform | internal |
| `valid_from` | `timestamptz` | 否 / `now()` | 部署开始 | platform | internal |
| `valid_to` | `timestamptz` | 是 | 非活动/退役时填写 | platform | internal |
| `row_version` | `bigint` | 否 / `1` | `>=1` | platform | internal |
| `created_at` | `timestamptz` | 否 / `now()` | 不可改 | platform | internal |
| `updated_at` | `timestamptz` | 否 / `now()` | 更新刷新 | platform | internal |

两个部分唯一索引分别保证 `(primary_agent_id) WHERE lifecycle_status='active'` 和 `(runtime_id) WHERE lifecycle_status='active'` 唯一，因此活动的 Primary Agent 与 Runtime 是一对一关系；历史部署仍可保留。`(primary_agent_id, primary_agent_structure_role)` 到 `AgentAsset(agent_id, structure_role)` 的复合外键既保证目标确实是 Primary，也阻止任何已被 Deployment 引用的 Agent 改成 Subagent。插入 Deployment 与角色修改使用 PostgreSQL 外键行锁串行化，不存在先检查后写入的竞态窗口。

### 8.2 `asset.agent_deployment_member`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `deployment_member_id` | `uuid` | 否 | PK | platform | internal |
| `agent_deployment_id` | `uuid` | 否 | FK AgentDeployment | import/platform | internal |
| `runtime_id` | `uuid` | 否 | 与 deployment 组成复合 FK，冗余用于 Runtime 内唯一约束 | import/platform | internal |
| `agent_id` | `uuid` | 否 | FK AgentAsset | import/platform | internal |
| `member_role` | `agent_structure_role` | 否 | 与 Agent.structure_role 一致 | import/platform | internal |
| `parent_member_id` | `uuid` | 是 | Subagent 指向本 Deployment 主成员 | import/platform | internal |
| `runtime_agent_key` | `text` | 是 | Runtime 内部稳定键；verified 时必填 | import/platform/discovery | internal |
| `mapping_status` | `deployment_member_mapping_status` | 否 / `planned` | 映射确认状态 | import/platform/discovery | internal |
| `config_ref` | `text` | 是 | 仅保存配置路径/引用 | import/platform | restricted |
| `valid_from` | `timestamptz` | 否 / `now()` | 生效时间 | platform | internal |
| `valid_to` | `timestamptz` | 是 | 晚于 valid_from | platform | internal |
| `row_version` | `bigint` | 否 / `1` | `>=1` | platform | internal |

`AgentDeployment` 增加 `UNIQUE (agent_deployment_id, runtime_id)` 作为复合外键目标，保证成员冗余的 runtime_id 与所属 Deployment 一致。活动成员以 `valid_to IS NULL` 表示；PostgreSQL 部分唯一索引保证同一 Runtime 的活动非空 `runtime_agent_key` 唯一，并保证一个 Deployment 最多一个活动 Primary 成员。

领域服务继续保证每个活动 Deployment 至少有一个且恰好一个 Primary 成员、该成员等于 `primary_agent_id`，以及 Subagent 是该 Primary 的直接子 Agent。`verified` 时 key 必填，`unresolved` 必须产生数据质量问题。

## 9. 网络需求与外部标识

### 9.1 `asset.agent_network_requirement`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `network_requirement_id` | `uuid` | 否 | PK | platform | restricted |
| `agent_id` | `uuid` | 否 | FK AgentAsset | import/platform | restricted |
| `system_name` | `text` | 否 | 目标系统 | import/platform | internal |
| `target_type` | `network_target_type` | 否 | 受控枚举 | import/platform | restricted |
| `target_value` | `text` | 否 | 按类型校验 | import/platform | restricted |
| `protocol` | `network_protocol` | 否 | 受控枚举 | import/platform | internal |
| `port_from` | `integer` | 是 | `1..65535` | import/platform | restricted |
| `port_to` | `integer` | 是 | `>=port_from` | import/platform | restricted |
| `purpose` | `text` | 是 | 用途 | import/platform | internal |
| `valid_from` | `timestamptz` | 否 / `now()` | 生效时间 | platform | internal |
| `valid_to` | `timestamptz` | 是 | 晚于 valid_from | platform | internal |
| `row_version` | `bigint` | 否 / `1` | `>=1` | platform | internal |

### 9.2 `asset.external_identifier`

统一保存旧系统编码。字段包括 `source_system`、`identifier_type`、`identifier_value`、生效区间，以及以下目标外键之一：Environment、CloudProvider、Server、ManagementGroup、Runtime、Agent、Department 或 Person。

约束：

- `num_nonnulls(...) = 1`，每条标识只指向一个对象。
- 活动 `(source_system, identifier_type, identifier_value)` 唯一。
- 失效标识不得自动复用。
- 名称、IP 和工作账号不通过 ExternalIdentifier 匹配。
- 初始迁移的 Agent Runtime 槽位种子仅用于一次性 UUID 冻结；`runtime_seed_key` / `agent_seed_key` 只允许完全相同初始计划幂等重试。首次提交后不得重新计算该种子匹配对象，平台 UUID 是权威身份。

## 10. 审计与导入暂存

### 10.1 `audit.change_log`

| 字段 | 类型 | 可空/默认 | 约束 | 来源 | 敏感级别 |
| --- | --- | --- | --- | --- | --- |
| `change_id` | `uuid` | 否 | PK，只追加 | platform | restricted |
| `occurred_at` | `timestamptz` | 否 / `now()` | 不可改 | platform | internal |
| `actor_type` | `text` | 否 | `user`, `import`, `migration`, `system` | platform | internal |
| `actor_ref` | `text` | 否 | 操作者/批次/服务引用 | platform | restricted |
| `source` | `text` | 否 | `ui`, `excel`, `migration`, `discovery` | platform | internal |
| `object_type` | `text` | 否 | 受控对象类型 | platform | internal |
| `object_id` | `uuid` | 否 | 目标对象 | platform | internal |
| `operation` | `change_operation` | 否 | 受控枚举 | platform | internal |
| `changed_fields` | `text[]` | 否 / `{}` | 字段路径 | platform | restricted |
| `before_value` | `jsonb` | 是 | 脱敏前值 | platform | restricted |
| `after_value` | `jsonb` | 是 | 脱敏后值 | platform | restricted |
| `import_batch_id` | `uuid` | 是 | 导入批次 | platform | internal |
| `reason` | `text` | 是 | 原因 | platform | restricted |
| `correlation_id` | `uuid` | 否 | 同一事务关联值 | platform | internal |

ChangeLog 不提供 UPDATE/DELETE 权限。

### 10.2 导入暂存表

沿用四张逻辑表：

- `ingest.import_batch`：文件、哈希、模板版本、import_mode、状态和汇总。
- `ingest.import_row`：Sheet、行号、目标对象、expected_row_version、原始与规范化载荷。
- `ingest.import_issue`：warning/error、稳定错误码和字段。
- `ingest.import_change`：字段级 `set`, `clear`, `ignore`, `conflict` 差异及用户选择。

导入暂存不得直接修改主表；提交时重新校验 row_version 并在单个事务内写主数据和 ChangeLog。

## 11. 删除与权限原则

- 核心对象、部署、成员和历史默认 `ON DELETE RESTRICT`。
- 退役更新生命周期，不物理删除。
- RuntimeDiscoverySnapshot 和 ChangeLog 创建后只读。
- 业务/IT 管理员和可访问用户当前只是记录字段，不形成真实 RBAC。
- 任何 Secret 都不得进入主表、Excel、快照或审计正文。

## 12. 运维集成只读契约

### 12.1 `integration.observability_gateway_v2`

v1 视图保持原样供旧同步脚本使用；v2 是新运维消费者的版本化读取边界。它以 `asset.runtime_instance` 为基表，仅选择 `runtime_role='gateway'`，并依靠 Runtime 活动 Deployment 唯一约束和外部标识/发现快照的确定性单行选择，保证每个 `runtime_id` 恰好一行。管理数据库是资产身份和当前绑定的事实来源，时序遥测仍由 OTel、Prometheus 及其后端保存。

| 字段组 | 主要字段 | 语义 |
| --- | --- | --- |
| 稳定身份 | `runtime_id`, `gateway_id`, `gateway_id_source`, `gateway_id_type`, `server_id`, `primary_agent_id`, `agent_deployment_id` | `runtime_id` 是首要遥测关联键；Gateway ID 是外部运行身份；Agent/Deployment 表达当前活动绑定 |
| 展示 | `lobster_name`, `server_display_id`, `display_name`, `department_code`, `department_name` | 名称允许变化，`display_name` 固定按“龙虾名-服务器展示编号”派生，均不得作为匹配键 |
| 运行位置 | `monitor_host`, `monitor_port`, `deployment_mode`, `bind_address`, `runtime_account`, `config_path`, `container_name` | 仅提供非秘密连接和配置目标；路径、账号属于 restricted 数据 |
| 生命周期 | `environment_lifecycle_status`, `server_lifecycle_status`, `runtime_lifecycle_status`, `runtime_observed_status`, `agent_deployment_lifecycle_status`, `primary_agent_lifecycle_status` | 显式保留各层状态，避免用单个 Runtime 状态代替整条资产链状态 |
| 环境和版本 | `environment_code`, `environment_stage`, `region`, `operations_team`, `runtime_family`, `desired_version`, `actual_version` | 部署筛选、展示和版本核对 |
| 发现证据 | `discovery_snapshot_id`, `discovery_status`, `discovered_at`, `discovery_content_hash` | 最新不可变脱敏发现快照的摘要，不包含 raw payload |
| 同步证据 | `source_updated_at`, `source_revision`, `contract_content_hash` | 时间用于新鲜度展示；行版本用于排障；内容哈希用于完整快照语义变更检测 |

就绪字段定义：

- `identity_ready`：存在 Gateway 外部 ID、当前活动 Deployment 和主智能体。
- `lifecycle_ready`：Environment 为 active，Server 为 active/maintenance，Runtime 和当前主智能体均为 active。
- `otel_configuration_ready`：裸金属具有 `runtime_account` 和 `config_path`，或容器具有 `container_name`。
- `prometheus_target_ready`：具有 Server 内网地址和 Runtime 端口。
- `otel_ready = identity_ready AND lifecycle_ready AND otel_configuration_ready`。
- `prometheus_ready = identity_ready AND lifecycle_ready AND prometheus_target_ready`。
- `monitoring_eligible = otel_ready OR prometheus_ready`。

`readiness_reasons` 是稳定 `text[]` 原因码，分别覆盖身份缺失、生命周期不可监控、OTel 配置目标缺失和 Prometheus 抓取目标缺失。它报告全部未满足项，因此当一条采集路径已经可用、另一条路径仍缺字段时可以非空。

`contract_content_hash` 是视图语义字段的 SHA-256，不包含 `source_updated_at` 和 `source_revision`；无业务内容变化的时间或行版本变化不会改变哈希。外部标识本身没有 `updated_at`，所以消费者必须采用完整快照加内容哈希比较，不得只按 `source_updated_at` 做排他增量同步。

视图禁止出现含 `password`、`secret`、`token` 或 `credential` 语义的列，也不连接 `security` Schema。特别是 Prometheus 所需 Gateway token 继续由独立运维模块安全保存和校验，数据库侧 `prometheus_ready` 只代表管理模块能够确认的非秘密前置条件。
