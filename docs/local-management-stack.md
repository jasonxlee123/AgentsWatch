# AgentsWatch 本机联调环境

## 1. 目的和边界

该环境用于在开发机上联调五个服务组件及认证授权链路：

- PostgreSQL 16 核心资产数据库。
- FastAPI 管理 API（只读查询与显式开启的服务器管理操作）。
- 独立 MCP Streamable HTTP 服务（只通过 REST 调用管理 API）。
- 独立运维控制适配器（只读数据库契约、版本化同步和 Prometheus 身份指标）。
- Vite + React 管理台。

同时验证账号密码、一次性滑块与角色权限；认证能力属于管理 API，不是第六个独立服务。

当前不连接生产数据库或生产服务器。开发机被视为一台裸金属测试服务器：如果检测到本机 OpenClaw LaunchAgent 正在运行，启动器会把它接入预置的本机 Server/Runtime 资产，保存真实端口、launchd Label 和加密 WebUI 凭据。服务器及运行时操作使用本机 Ansible Runner，只有主动发起白名单标准操作时才会执行。管理台支持五类主数据的 Excel 上传预览和本机事务提交。

## 2. 前置条件

- Docker Desktop 和 Docker Compose。
- Python 3.12+。
- Node.js 和 npm。

启动器会在需要时自动创建 `.venv-api` 并安装 API、MCP 依赖，也会在 `frontend/node_modules` 缺失时运行 `npm install`。

默认启用本机 OpenClaw 接入；临时需要纯模拟基线时可设置：

```bash
AGENTSWATCH_ADOPT_LOCAL_OPENCLAW=false ./scripts/local_management_stack.sh start
```

## 3. 启动和停止

在项目根目录运行：

```bash
./scripts/local_management_stack.sh start
```

启动器在前台监督 API 和前端进程。启动成功后会打印实际地址、登录用户名和权限为 `0600` 的本机密码文件路径；脚本不会把随机密码写入日志。在该终端按 `Ctrl+C` 可停止整个本机环境。也可在首次启动前通过 `AGENTSWATCH_LOCAL_ADMIN_USERNAME` 和 `AGENTSWATCH_LOCAL_ADMIN_PASSWORD` 设置仅本机使用的凭据。不要把它们写入文档、提交或共享 shell history。

另一个终端中可以查看状态或停止：

```bash
./scripts/local_management_stack.sh status
./scripts/local_management_stack.sh stop
```

重置会停止进程、删除本机 PostgreSQL 数据卷并在下次启动时重建模拟数据：

```bash
./scripts/local_management_stack.sh reset
```

## 4. 端口策略

- 前端优先使用 `4173`。
- API 优先使用 `8000`。
- MCP 优先使用 `8010`。
- 运维控制适配器优先使用 `8011`。
- PostgreSQL 始终自动选择空闲本机端口。
- 前端、API、MCP 或运维控制适配器的首选端口已被占用时，启动器自动分配空闲端口。

实际端口保存在 `.local-data/management-stack/*.port`，前端代理会自动指向实际 API 端口。需要固定端口时可以显式设置：

```bash
AGENTSWATCH_LOCAL_FRONTEND_PORT=4173 \
AGENTSWATCH_LOCAL_API_PORT=18000 \
AGENTSWATCH_LOCAL_MCP_PORT=18010 \
AGENTSWATCH_LOCAL_OBSERVABILITY_PORT=18011 \
AGENTSWATCH_LOCAL_DB_PORT=15432 \
./scripts/local_management_stack.sh start
```

所有端口只绑定 `127.0.0.1`。

## 5. 数据和权限

PostgreSQL 容器首次初始化时依次执行：

1. `database/core-assets/001_core_asset_schema.sql`
2. `database/core-assets/002_server_management.sql`
3. `database/core-assets/004_runtime_management.sql`
4. `database/core-assets/005_runtime_web_access.sql`
5. `database/core-assets/006_agent_channels.sql`
6. `database/core-assets/007_subagent_channels.sql`
7. `database/core-assets/008_agent_operations.sql`
8. `database/core-assets/009_automation_library_and_tasks.sql`
9. `database/core-assets/010_operation_events.sql`
10. `database/core-assets/011_allow_shared_public_ip.sql`
11. `database/core-assets/012_systemd_user_runtime_management.sql`
12. `database/core-assets/013_user_authentication.sql`
13. `database/core-assets/014_organization_master_guards.sql`
14. `database/core-assets/015_runtime_primary_agent_one_to_one.sql`
15. `database/core-assets/016_runtime_management_groups.sql`
16. `database/core-assets/017_server_password_bootstrap.sql`
17. `database/core-assets/018_api_keys.sql`
18. `database/core-assets/019_operation_confirmations.sql`
19. `database/core-assets/020_observability_gateway_v2.sql`
20. `database/core-assets/021_agent_deployment_primary_role_integrity.sql`
21. `tests/sql/core_asset_schema_test.sql`
22. `database/core-assets/003_local_reader.sql`

第二十一个文件是 PostgreSQL 约束验证使用的模拟数据集，最终保留可供管理台查询的 Server、Runtime、Agent、管理分组、导入批次和数据质量问题。

以上 `docker-entrypoint-initdb.d` 文件只在空数据卷首次初始化时运行。为兼容已有本机数据卷，启动器会在数据库健康后、创建本机管理员和启动 API 前执行一次原地升级：先逐项核实现有 Schema 并建立迁移账本，再由正式迁移器只执行尚未应用的迁移，最后重放 reader/writer 最小权限策略。该过程可重复执行，不重建数据卷、不重新导入清单，也不删除业务数据；检测到非连续或无法核验的旧 Schema 时会停止启动并保留原数据。

查询使用 `agentswatch_reader` 账号：

- 仅有目标 Schema 的 `USAGE` 和表 `SELECT` 权限。
- 角色默认 `default_transaction_read_only=on`。
- API 每次事务再次设置只读。

运维控制适配器不复用该通用查询账号，而使用
`agentswatch_observability`：它在受管业务对象上的显式权限只有数据库 `CONNECT`、
`integration` Schema `USAGE` 和 `integration.observability_gateway_v2` 的 `SELECT`；不能直接读取
`asset`、`org`、`security` 或任何凭据表。业务数据只读由对象 ACL 强制，
`default_transaction_read_only=on` 只是防误操作默认值，不能作为权限边界。专用角色配置还会拒绝
该账号能够有效执行的 `SECURITY DEFINER` 函数，防止从 `PUBLIC EXECUTE` 等授权形成绕过路径，
并拒绝额外关系/列/序列权限或受管 Schema 的 `CREATE`；它不会修改数据库的全局 `PUBLIC` 策略，
因此无提权能力的公共函数或 `public` Schema `USAGE` 等环境级授权可能仍然继承。

服务器、运行时、智能体和认证数据使用独立的 `agentswatch_app` 账号，只授予资产、审计、自动化任务与 `security` Schema 所需的读写权限；只读数据库账号不能读取密码哈希、会话或服务器 SSH 密码密文。API 进程还必须设置 `AGENTSWATCH_ENABLE_MANAGEMENT=true` 才会开放写操作；本机启动器会显式设置该开关，且所有端口仅绑定回环地址。服务器长期 SSH 密码、运行时 Web 凭据和智能体渠道 AppSecret 分别使用本机提供的对称密钥加密，详情 API 与审计均不返回明文或密文字段；服务器 SSH 密码只供首次或显式重新纳管使用，普通任务固定使用平台公钥。

用户角色为：`admin` 可管理全部模块和用户，`operator` 可维护资产并执行任务，`viewer` 只读。会话使用 HttpOnly Cookie，写请求另行校验 CSRF Token；每位用户还可创建一把与角色权限一致、可刷新和撤销的长期 API Key，供 REST 与 MCP 共用。连续 5 次密码错误会锁定账号 15 分钟。

## 6. 验证

完整本机联调验证：

```bash
./scripts/test_local_management_stack.sh
```

只验证“本机裸金属 OpenClaw 纳管 → 管理端预览/确认 → 运维版本激活 →
`agentswatch_gateway_info` 身份指标 → Prometheus rule reload → OpenClaw 补丁 dry-run”的独立链路：

```bash
./scripts/test_observability_integration_local.sh
```

管理栈已经运行时，也可以只验证本机真实 OpenClaw，不重建数据库或前端：

```bash
AGENTSWATCH_API_USERNAME=<local-admin-username> \
AGENTSWATCH_API_PASSWORD=<local-admin-password> \
AGENTSWATCH_WRITE_DATABASE_URL=<local-writer-dsn> \
.venv-api/bin/python scripts/verify_local_openclaw_management.py \
  --api-url http://127.0.0.1:8000
```

如果 API 使用了自动分配端口，请以启动器输出或 `.local-data/management-stack/api.port` 为准。验证脚本不输出保存的凭据，且无论中途是否失败都会重新启用本机 OpenClaw LaunchAgent。

该脚本会重建本机模拟数据库，验证：

- 数据库健康状态和只读账号。
- 未登录访问、滑块登录、会话、CSRF 与角色越权拦截。
- API Key 生命周期、Bearer REST 调用、MCP 健康检查与未携带 Key 时的拒绝。
- 服务器新增、修改、连接配置、Ansible 操作和退役闭环。
- 裸金属运行时新增、修改、systemd、launchd、本机 Mock 操作和退役闭环。
- 对本机真实 OpenClaw 执行信息采集、快照去重、状态刷新、停止、启动和重启，核对 LaunchAgent 进程、16402 端口及 WebUI 恢复情况。

持久卷原地升级另使用隔离的临时 PostgreSQL 验证，不触碰当前本机管理数据：

```bash
./scripts/test_local_persistent_database_upgrade.sh
```
- 运行时 WebUI 加密凭据、模拟页面打开、Token fragment、访问会话关闭和历史记录闭环。
- 总览数量、管理分组成员和数据质量载荷。
- 前端可访问性。
- 全部前端单元测试和生产构建。

测试结束后会停止环境，但保留 PostgreSQL 数据卷。

## 7. 日志

- `.local-data/management-stack/api.log`
- `.local-data/management-stack/frontend.log`
- `.local-data/management-stack/observability-control.log`
- `.local-data/management-stack/supervisor-test.log`

这些文件已被 Git 忽略。
