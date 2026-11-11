# 核心资产 PostgreSQL Schema 与迁移

本目录保存核心资产数据架构的 PostgreSQL 16 有序迁移、权限配置和实现级验证。生产由迁移器显式应用这些文件；API 启动不会隐式迁移数据库。完整部署步骤见[部署指引](../../docs/deployment-guide.md)。

## 文件

- `001_core_asset_schema.sql`：创建核心资产、组织、审计和导入模型。
- `002_server_management.sql`：增加服务器 Ansible 连接、操作任务和逐服务器执行结果。
- `004_runtime_management.sql`：增加运行时管理驱动，并让统一操作任务支持运行时目标。
- `005_runtime_web_access.sql`：增加运行时类型适配元数据、WebUI 入口、加密凭据和临时访问会话。
- `006_agent_channels.sql`：增加主智能体渠道配置、飞书 AppID 唯一约束和加密 AppSecret 字段。
- `007_subagent_channels.sql`：允许子智能体拥有独立渠道配置，不再强制继承主智能体渠道。
- `008_agent_operations.sql`：让统一操作运行结果关联智能体及其部署。
- `009_automation_library_and_tasks.sql`：增加自动化定义、不可变发布版本，以及任务版本、重试和取消关联。
- `010_operation_events.sql`：增加不可变任务执行事件，保存安全裁剪后的 Ansible Play、Task、Host、输出和阶段信息。
- `011_allow_shared_public_ip.sql`：允许 NAT/共享出口场景下多台活动服务器共用公网 IP，同时保留查询索引。
- `012_systemd_user_runtime_management.sql`：增加 systemd 用户服务管理驱动及可选运行命令绝对路径，并保持裸金属约束。
- `013_user_authentication.sql`：增加管理用户、一次性滑块挑战和服务端会话，支持管理员、操作员和只读角色。
- `014_organization_master_guards.sql`：在数据库层禁止修改部门编码/人员工号及物理删除组织主数据，停用统一使用状态字段。
- `015_runtime_primary_agent_one_to_one.sql`：强制一个 Runtime 最多一个生效主智能体，并提供一行一个 Gateway 的运维集成只读视图。
- `016_runtime_management_groups.sql`：补充运行时管理分组成员关系，让 Server、Runtime、Agent 都可作为静态批量管理目标集。
- `017_server_password_bootstrap.sql`：增加加密 SSH 密码、主机指纹固定和“密码首次建联、公钥复验后纳管”的状态字段。
- `018_api_keys.sql`：增加用户 API Key 的哈希存储、轮换历史及“每用户仅一个有效 Key”约束。
- `019_operation_confirmations.sql`：增加与用户、方法、路径、查询和请求体指纹绑定的一次性操作确认记录。
- `020_observability_gateway_v2.sql`：保留 v1 并增加生产运维读取契约，拆分身份、生命周期、OTel 和 Prometheus 就绪状态，提供稳定原因码与内容哈希。
- `021_agent_deployment_primary_role_integrity.sql`：用固定角色判别列和复合外键保证 Deployment 只能引用 Primary Agent，并由 PostgreSQL 外键锁串行化并发绑定与角色变更。
- [`../../tests/sql/core_asset_schema_test.sql`](../../tests/sql/core_asset_schema_test.sql)：有效数据、非法约束、历史迁移和不可变性测试。
- [`../../scripts/test_core_asset_postgres.sh`](../../scripts/test_core_asset_postgres.sh)：启动隔离临时 PostgreSQL、执行 DDL/测试/并发验证并自动删除容器。
- [`../../scripts/apply_core_asset_migrations.py`](../../scripts/apply_core_asset_migrations.py)：按校验和和版本账本串行应用正式迁移；自动排除仅供本机使用的 `003_local_reader.sql`。
- [`../../scripts/configure_core_asset_roles.py`](../../scripts/configure_core_asset_roles.py)：在迁移完成后配置独立 reader/writer 登录角色、当前对象授权和 migration owner 默认权限。
- [`../../scripts/adopt_gateway_identities.py`](../../scripts/adopt_gateway_identities.py)：把旧运维清单中的 Gateway ID 按稳定 Server external identifier 生成只读计划，再经文件 SHA 确认后以 append-only 与审计记录提交；host/port 只用于阻断一致性校验。
- [`../../scripts/test_core_asset_role_grants.sh`](../../scripts/test_core_asset_role_grants.sh)：在隔离 PostgreSQL 16 中验证角色幂等配置、当前/未来表权限和越权拒绝。

## 本机运行

前提是 Docker 可用且本机存在 `postgres:16-alpine` 镜像：

```bash
./scripts/test_core_asset_postgres.sh
```

测试容器具有以下隔离条件：

- 使用唯一临时容器名。
- `--network none`，不发布宿主机端口。
- 不挂载数据卷，退出时自动删除。
- 不连接现有监控栈或其他 PostgreSQL 容器。
- 只写入模拟数据，不读取或修改 `cluster/lobsters.json`。

可通过环境变量替换本地已有镜像及其架构：

```bash
POSTGRES_IMAGE=postgres:16-alpine \
POSTGRES_PLATFORM=linux/amd64 \
./scripts/test_core_asset_postgres.sh
```

`001_core_asset_schema.sql` 是首版迁移，不包含 DROP，也不设计为在已有 Schema 上重复执行。后续变更应新增有序迁移文件，不能直接修改已经部署过的迁移。

部署时通过环境变量提供具备建表权限的 PostgreSQL 16 连接，凭据不进入命令行：

```bash
AGENTSWATCH_MIGRATION_DATABASE_URL='postgresql://...' \
  python scripts/apply_core_asset_migrations.py --plan
AGENTSWATCH_MIGRATION_DATABASE_URL='postgresql://...' \
  python scripts/apply_core_asset_migrations.py
```

执行器使用数据库 advisory lock 防止并发迁移；已经应用的文件若校验和发生变化会立即失败。目标数据库已有 `org`、`asset`、`audit`、`ingest`、`automation`、`security` 或 `integration` Schema、但没有迁移账本时也会拒绝覆盖，必须先人工核实来源。

## API 数据库角色授权

数据库迁移与 API 运行使用不同角色。迁移完成后，由数据库管理员执行独立授权脚本；管理员 DSN、migration owner、reader/writer 角色名和两个密码都只能通过环境变量提供：

```bash
export AGENTSWATCH_DATABASE_ADMIN_URL='postgresql://...'
export AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE='agentswatch_migration_owner'
export AGENTSWATCH_DATABASE_READER_ROLE='agentswatch_reader'
export AGENTSWATCH_DATABASE_WRITER_ROLE='agentswatch_writer'
export AGENTSWATCH_DATABASE_READER_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AGENTSWATCH_DATABASE_WRITER_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

python scripts/configure_core_asset_roles.py

unset AGENTSWATCH_DATABASE_ADMIN_URL \
  AGENTSWATCH_DATABASE_READER_PASSWORD \
  AGENTSWATCH_DATABASE_WRITER_PASSWORD
```

授权矩阵如下：

| Schema | Reader | Writer |
| --- | --- | --- |
| `org` | `SELECT` | `SELECT, INSERT, UPDATE` |
| `asset` | `SELECT` | `SELECT, INSERT, UPDATE` |
| `audit` | `SELECT` | `SELECT, INSERT` |
| `ingest` | `SELECT` | `SELECT, INSERT, UPDATE`（Excel 预览暂存与整批提交状态） |
| `automation` | `SELECT` | `SELECT, INSERT, UPDATE` |
| `security` | 无访问权限 | `SELECT, INSERT, UPDATE, DELETE` |
| `integration` | `SELECT` | `SELECT` |

Reader 默认事务只读；两个 API 角色都没有 `SUPERUSER`、`CREATEDB`、`CREATEROLE`、`REPLICATION`、`BYPASSRLS`、数据库 `CREATE/TEMPORARY`、Schema `CREATE`、对象所有权或角色继承关系。脚本会清理目标数据库和七个受管 Schema 的 `PUBLIC` 默认访问，再按上表授权，因此目标应是 AgentsWatch 专用数据库，不应与其他应用共享。

`AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE` 必须拥有 `org`、`asset`、`audit`、`ingest`、`automation`、`security` 和 `integration` Schema，后续迁移也必须继续以该角色创建对象。脚本同时设置此 owner 的 `ALTER DEFAULT PRIVILEGES`，保证未来表继承同一矩阵；API 角色不获得序列或函数权限，并会撤销该 owner 创建的新函数默认授予 `PUBLIC` 的执行权。若 owner、Schema 所有权或既有 API 角色不安全，脚本会整体回滚而不是接管角色。脚本可以重复执行，用于密码轮换和权限收敛，成功或失败输出都不会打印 DSN 或密码。

## 运维模块数据库契约

`integration.observability_gateway_v1` 为既有脚本保留，不修改字段或语义。新消费者使用 `integration.observability_gateway_v2`：视图只包含 `runtime_role='gateway'` 的 Runtime，每个 `runtime_id` 恰好一行；活动部署存在时带出稳定的 `primary_agent_id`、`agent_deployment_id`，以及 `lobster_name-server_display_id` 形式的 `display_name`。`gateway_id` 优先读取正式 `gateway_id` 外部标识，兼容回退到 `legacy_runtime_id`，不会把导入 seed key 冒充 Gateway 身份。

v2 额外提供 `runtime_account`、`config_path`、`container_name` 和各层生命周期，并把状态拆成：

- `identity_ready`：Gateway ID、当前活动 Deployment 和主智能体身份完整。
- `lifecycle_ready`：Environment、Server、Runtime 和主智能体均处于可监控生命周期。
- `otel_ready`：身份、生命周期以及裸金属账号/配置路径或容器名等非秘密配置目标齐备；不代表已经收到实时遥测。
- `prometheus_ready`：身份、生命周期、内网抓取地址和端口齐备；Gateway token 是否已安全部署由运维模块自行检查。
- `monitoring_eligible`：OTel 或 Prometheus 至少一条管理侧路径就绪。
- `readiness_reasons`：所有未满足条件的稳定原因码；某一路可用而另一路不完整时仍可能非空。

运维模块使用独立只读数据库账号查询视图，不直接写管理资产表。`source_updated_at` 用于显示源数据新鲜度，完整快照同步应比较 `contract_content_hash`，不要把时间戳单独作为无遗漏增量游标。`source_revision` 只含行版本和发现快照 ID，便于排障。两个视图都不暴露 SSH 凭据、Gateway token、密码、私钥、WebUI 凭据或智能体渠道 Secret。OTel/Prometheus 时序数据继续以 `runtime_id/gateway_id` 为颗粒度，`primary_agent_id` 用于稳定资产关联，名称和 `display_name` 只用于展示。

专用运维账号由 `scripts/configure_observability_integration_role.py` 收敛权限。业务数据只读的安全边界是对象 ACL：受管 Schema 中唯一允许的关系权限是 `integration.observability_gateway_v2` 的 `SELECT`。`default_transaction_read_only=on` 只是防误操作的会话默认值，账号能够修改自己的会话默认值，不能替代对象授权。配置脚本不会破坏性修改数据库的全局 `PUBLIC` 权限，但会拒绝任何可由该账号有效执行的 `SECURITY DEFINER` 函数，避免从 `PUBLIC EXECUTE` 等继承授权形成提权路径。

隔离验证命令：

```bash
./scripts/test_core_asset_role_grants.sh
```

该测试创建一次性 PostgreSQL 16 容器，执行正式迁移，连续运行两次授权脚本，并以 reader/writer 实际验证当前表、migration owner 后续创建的表、只读默认值，以及 DDL、角色管理、数据库管理和越权 DML 均被拒绝。

## 当前边界

迁移与测试已验证数据库可以直接保证的约束。Deployment 的 `primary_agent_id` 通过复合外键只能引用 Primary Agent，且有任意历史或当前 Deployment 引用时不能把该 Agent 改成 Subagent。其他跨行生命周期转换、Subagent 父级角色/服务类型一致等规则由领域服务在事务中保证；ManagementGroup 的资源类型、成员资格和静态成员变更已由管理服务统一校验并审计。

Schema 只保存管理用户的 scrypt 密码哈希，不保存明文密码；管理员 DSN 和 API 角色密码由部署环境短暂注入。连接池、备份、监控和 Excel 提交执行器属于应用或部署层，不在本目录的实现范围内。
