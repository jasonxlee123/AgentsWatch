# 运维清单控制适配器

> 本文是适配器的实现级专项手册。联合部署和运维独立部署的完整顺序请先阅读[部署指引](../docs/deployment-guide.md)。根运维 Compose 不启动本适配器；[`deploy/combined`](../deploy/combined/README.md) 会在联合模式中增加专用适配器容器。

该服务属于运维模块。联合模式把它作为独立容器纳入统一 Compose，其他模式可作为独立进程部署。管理模块只调用固定 HTTP API；适配器自行以专用只读账号读取
`integration.observability_gateway_v2`，不会读取或接收 SSH 密码、私钥、Gateway Token、WebUI 凭据或智能体渠道 Secret。

核心库、CLI 和 FastAPI 使用同一套流程：读取全量快照、按 `runtime_id` 对账、生成候选修订、校验全部产物、原子切换
`current` 符号链接并保留 `last-known-good`。数据库或 Prometheus 暂时不可用时，不会用空清单覆盖当前生产修订。

## 安装与启动

```bash
python3 -m venv .venv-observability-control
.venv-observability-control/bin/pip install -r requirements-observability-control.txt
```

生产环境变量：

```bash
AGENTSWATCH_OBSERVABILITY_CONTROL_ROOT=/srv/agentswatch/observability-control
AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL='<observability-reader-dsn-from-secret-store>'
AGENTSWATCH_OTEL_ENDPOINT='https://monitor.example.invalid:4318'
AGENTSWATCH_OBSERVABILITY_RUNTIME_FAMILY=openclaw
AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY=false
AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY=false
AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN='<generated-bearer-token>'
AGENTSWATCH_PROMETHEUS_RELOAD_URL='http://127.0.0.1:9090/-/reload'
```

上述路径适用于宿主机独立进程。联合 Compose 会把 `AGENTSWATCH_DATABASE_CA_HOST_FILE` 挂到 `/run/secrets/agentswatch-database-ca.crt`，因此联合 env 的 DSN 使用该容器内路径。两种方式都必须以证书 SAN 中的数据库主机名连接并保持 `sslmode=verify-full`。

两个安全覆盖在生产必须保持 `false`：空或全部阻断的来源需要 empty override，移除任何此前可部署 Runtime 需要 destructive override，完整下线同时需要两者。所有覆盖只在经审批的操作窗口短时启用，完成后立即恢复。

数据库账号应设置 `default_transaction_read_only=on`，并且在受管业务关系上只授予
`observability_gateway_v2` 的 SELECT。不要复用管理 API 的通用 reader/writer 账号。生产授权使用：

```bash
AGENTSWATCH_DATABASE_ADMIN_URL='<database-admin-dsn-from-secret-store>' \
AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE=agentswatch_migration_owner \
AGENTSWATCH_OBSERVABILITY_DATABASE_ROLE=agentswatch_observability \
AGENTSWATCH_OBSERVABILITY_DATABASE_PASSWORD='<generated-role-password>' \
  python scripts/configure_observability_integration_role.py
```

这里的强制安全边界是 PostgreSQL 对象 ACL，而不是 `default_transaction_read_only`：登录账号可以修改自己的会话默认值，
所以后者只用于防误操作。授权脚本逐项确认受管 Schema 中唯一有效的关系权限是 v2 视图的 `SELECT`，并拒绝账号可执行的
任何 `SECURITY DEFINER` 函数、额外列/序列权限和受管 Schema `CREATE`，以防 `PUBLIC EXECUTE` 或其他继承授权绕过
对象 ACL；脚本不会全局撤销 `PUBLIC` 权限，无提权能力的公共函数或 `public` Schema `USAGE` 可能仍按数据库策略继承。

单 worker 启动：

```bash
.venv-observability-control/bin/python -m agentswatch_observability serve \
  --host 127.0.0.1 \
  --port 8011
```

仅运维模式若需要长期 API，可用 systemd 或独立容器运行该命令；跨主机调用时应通过受保护的 HTTPS 入口和来源 ACL，
不得直接发布 `8011`。联合模式使用 `deploy/combined/Dockerfile.observability-control`，适配器只加入运维网络；管理 API 经共享 Nginx 未发布的内部 `8080` listener 和 Bearer Token 调用，不把控制 API 暴露为公网路径。

## API

配置 `AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN` 后，下列接口都要求
`Authorization: Bearer <token>`；`/healthz` 始终不返回资产和秘密，可用于本地健康检查。

- `GET /api/v1/inventory/status`
- `POST /api/v1/inventory/sync:preview`
- `POST /api/v1/inventory/sync`
- `GET /api/v1/jobs/{job_id}`
- `POST /api/v1/inventory/rollback/{revision_id}`
- `GET /metrics`

preview/sync 请求体可省略；默认数据源是管理数据库：

```json
{
  "source": "management-db",
  "expected_snapshot_hash": "可选的preview返回值"
}
```

正式同步建议同时发送唯一的 `Idempotency-Key`。同一个 Key 和同一个请求会返回原 Job；同一个 Key 被用于不同请求时返回
HTTP 409。同步当前是短事务同步执行，但仍返回持久化 Job，管理模块可以统一通过 Job 接口展示结果。

## CLI 与无数据库模式

数据库模式：

```bash
./scripts/observabilityctl.py preview --source management-db
./scripts/observabilityctl.py sync --source management-db \
  --expected-snapshot-hash <preview输出的snapshot_hash>
./scripts/observabilityctl.py status
./scripts/observabilityctl.py rollback <revision_id>
```

没有管理数据库时继续使用现有 `cluster/lobsters.json`：

```bash
./scripts/observabilityctl.py sync \
  --source local-json \
  --inventory cluster/lobsters.json \
  --otel-endpoint https://monitor.example.invalid:4318
```

旧清单没有 `runtime_id` 时，适配器使用稳定的 `standalone:<gateway_id>` 作为本地 Runtime 键，不写回源文件。
`lobsterctl.py` 和 `apply_lobster_config.sh` 仍可独立使用；控制适配器生成的补丁位于：

```text
/srv/agentswatch/observability-control/current/openclaw-monitoring-patches/
```

## 运维策略 Overlay

管理数据库只拥有资产事实。采样率、Gateway bind 和原生 Prometheus 直抓等策略保存在运维侧 Overlay，默认路径为
`$AGENTSWATCH_OBSERVABILITY_CONTROL_ROOT/overlay.json`：

```json
{
  "runtimes": {
    "管理数据库中的runtime_id": {
      "native_scrape_enabled": true,
      "scrape_host": "host.docker.internal",
      "sample_rate": 1.0
    }
  }
}
```

原生直抓默认关闭。只有显式开启且 v2 契约的 `prometheus_ready=true` 时才生成带 Token 文件引用的 scrape job；OTel
接入不要求 Prometheus Token。`scrape_host`/`scrape_port` 只覆盖 Prometheus 所见的目标地址，不修改管理数据库中的资产地址；
例如 Prometheus 运行在 Docker Desktop 中、Gateway 运行在宿主机 loopback 时，可将 `scrape_host` 设置为
`host.docker.internal`，而不需要把 Gateway 改为局域网监听。

## Prometheus 接入

当前 revision 会生成 `agentswatch-gateway-info.rules.yml`，按活动全量快照输出真实的 `agentswatch_gateway_info`，包含
Runtime、Gateway、Server、主智能体、活动 Deployment、龙虾名称、展示名称、集群、环境、部门、地域、团队、部署方式和
清单状态。与 Prometheus 同机部署时，推荐把适配器状态根目录只读挂到 Prometheus，并加载
`current/*.rules.yml`；revision 原子切换后调用 `/-/reload`，即使适配器进程短暂不可用，当前身份指标也继续存在。

若适配器与 Prometheus 不共享运维数据盘，可改为抓取受 Bearer Token 保护的 `GET /metrics`。Grafana 用该指标做资产筛选
和“龙虾名-服务器ID”关联，不再依赖 Gateway 原生直抓是否在线。

```yaml
- job_name: agentswatch-observability-control
  metrics_path: /metrics
  authorization:
    credentials_file: /etc/prometheus/secrets/observability-control.token
  static_configs:
    - targets: ["observability-control.internal:8011"]
```

规则文件和 `/metrics` 两种方式二选一，不能同时启用，以免产生两份来源不同但语义相同的资产序列。

Prometheus 配置通过 `scrape_config_files` 加载
`/etc/prometheus/agentswatch-control/current/openclaw-gateways.yml`，规则和补丁同样通过挂载整个状态根目录后引用
`current/...`；不要把 `current` 的目标文件单独 bind mount，
否则容器运行时可能固定旧 inode。同步在候选修订校验通过后一次切换 `current`，随后调用可选的 Prometheus reload URL；
reload 失败会自动恢复先前活动修订。

状态根目录、`revisions/`、`staging/` 和具体 revision 使用 writer 所属组的 `2750`，产物显式使用 `0640`；即使
systemd 配置 `UMask=0077`，Prometheus 仍可通过附加 writer GID 只读访问，其他本机用户不可读。Darwin 本地进程不支持
setgid 目录时退化为同组 `0750`。Token 始终只存在于独立的 `/etc/prometheus/secrets/gateways/*.token` 挂载中，并保持
`0640`。
`storage-init` 默认把状态写目录交给独立适配器账号 `10001:10001`。若适配器作为宿主机 systemd 服务或本地进程运行，
必须将 `AGENTSWATCH_OBSERVABILITY_CONTROL_UID`、`AGENTSWATCH_OBSERVABILITY_CONTROL_GID` 设置为该进程用户的
实际数字 UID/GID；初始化只设置明确的 writer ownership，不使用 world-writable 权限。
