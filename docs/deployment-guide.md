# AgentsWatch 部署指引

本文是生产部署的统一入口，覆盖以下三种方式：

1. 管理模块和运维模块同时部署；
2. 只部署管理模块；
3. 只部署运维模块。

三种方式都基于当前仓库的实际交付物。系统架构和模块边界见[系统架构](system-architecture.md)，本机开发环境见[本机联调环境](local-management-stack.md)。

## 1. 先选择部署模式

| 能力或组件 | 管理 + 运维 | 仅管理 | 仅运维 |
| --- | :---: | :---: | :---: |
| PostgreSQL 16+（外部或独立本机 Compose） | 必需 | 必需 | 不需要 |
| 管理 API、MCP | 部署 | 部署 | 不部署 |
| 用户 HTTPS 入口 | 共享 Nginx `443` | 管理 Nginx `8443` | 运维 Nginx `443` |
| 资产、组织、分组、批量导入 | 可用 | 可用 | 不可用 |
| 自动化库、任务中心、SSH 纳管 | 可用 | 可用 | 不可用 |
| OTel、Prometheus、Thanos、Tempo、Loki、ClickHouse | 部署 | 不部署 | 部署 |
| Grafana 与告警计算 | 可用 | 不可用 | 可用 |
| 运维控制适配器 | 联合 Compose 内的专用容器 | 不部署 | CLI 按需运行；API 可选 |
| 监控清单事实来源 | 管理 DB v2 视图 | 无 | `cluster/lobsters.json` |
| 管理页“可观测接入” | 可用 | 显示未配置 | 无管理页 |

推荐原则：

- 需要完整资产管理、批量操作和统一观测时，选择“管理 + 运维”。
- 只需要资产、自动化或 MCP，不建设遥测后端时，选择“仅管理”。
- 已有独立清单，只需要采集、存储与 Grafana 时，选择“仅运维”。

## 2. 当前交付边界

四个联合生产文件分别承担不同职责：

- `deploy/management/compose.production.example.yml` 只启动管理 API、MCP 和管理 Nginx，PostgreSQL 是外部依赖；
- 根目录 `docker-compose.yml` 只启动运维遥测数据栈；
- `deploy/combined/compose.production.example.yml` 是联合覆盖：禁用两个独立 Nginx，增加适配器容器与共享 Nginx，并划分管理/运维后端网络；
- `deploy/combined/compose.production-lockdown.yml` 是生产锁定覆盖：强制上游镜像和服务凭据显式提供，并禁止生产容器拉取浮动镜像。

联合模式用同一个项目名按顺序叠加这四个文件，只有 `combined-nginx` 发布用户 HTTPS `443`；OTLP `4318` 仍按数据面需求单独开放，管理端口保持回环绑定。仅管理和仅运维模式继续单独使用各自基础 Compose。根运维 Compose 本身仍不包含适配器；仅运维模式如果要长期开放适配器 API，需要另外提供 supervisor 和安全入口。

PostgreSQL 必须部署在同一台主机时，使用 `deploy/postgres/compose.production.yml` 单独启动数据库项目，并在 lockdown 之后追加 `deploy/postgres/compose.application-network.yml`。需要保留旧 `/openclaw/` 路由时再把 `deploy/combined/compose.legacy-openclaw-relay.yml` 放在最后。这些覆盖一旦启用，后续所有 `config`、`run`、`up`、`exec` 和 `down` 命令都必须保持同样的文件顺序；详见[单机 PostgreSQL 说明](../deploy/postgres/README.md)。

## 3. 通用生产要求

### 3.1 软件与基础设施

- Linux 主机和 Docker Engine，支持 `docker compose` 插件；
- 管理模式需要 PostgreSQL `16+`；可使用外部数据库，也可使用仓库提供的独立本机 Compose；
- 管理模式需要 PostgreSQL 服务端证书对应的受信 CA 文件；
- 组织签发的 TLS 证书；仓库自动生成的自签证书只适合验收环境；
- 可写且可备份的数据盘，例如 `/srv/agentswatch`；
- 固定的 DNS、NTP 和时区策略；
- 从管理节点到受管服务器的 SSH 网络，以及从 OpenClaw 到 OTLP `4318` 的网络；
- 能为所有密码、Token、Fernet key、私钥和 DSN 提供受控秘密存储。

资源规格取决于资产数、指标基数、日志量、trace 采样率和保留周期。上线前应按[遥测数据持久化](data-persistence.md)估算容量，并为数据盘设置容量告警。

### 3.2 版本与镜像

生产必须使用明确版本或 digest，不能沿用 `.env.example` 中多数镜像的 `latest` 默认值。至少固定：

- `MINIO_IMAGE`、`MINIO_MC_IMAGE`；
- `OTEL_COLLECTOR_IMAGE`、`PROMETHEUS_IMAGE`、`ALERTMANAGER_IMAGE`；
- `THANOS_IMAGE`、`LOKI_IMAGE`、`TEMPO_IMAGE`；
- `GRAFANA_IMAGE`、`NGINX_IMAGE`、`CLICKHOUSE_IMAGE`；
- `AGENTSWATCH_MANAGEMENT_NGINX_IMAGE`（仅管理模式）；
- `AGENTSWATCH_MANAGEMENT_IMAGE`（仅管理与联合模式）；
- `AGENTSWATCH_OBSERVABILITY_CONTROL_IMAGE`（联合模式）。

部署记录应保存 Git commit、管理镜像 digest、运维镜像清单、生产预检生成的本地 image ID 记录、数据库迁移最高版本和配置备份时间。

共享或仅管理 Nginx 必须保留可审计的 `nginx` worker 数字 GID。官方 `nginx:stable-alpine` 当前使用 `101`；如果固定到其他兼容镜像或 digest，先执行 `docker run --rm --entrypoint getent <Nginx镜像> group nginx`，再把输出的数字 GID 写入 `AGENTSWATCH_NGINX_GID`。该值同时控制管理 UDS 目录组，二者不一致会导致入口返回 `502`。

### 3.3 安全基线

- 联合模式共享一个 Nginx、一个项目名和一套入口证书，但管理/运维后端网络、数据目录、数据库账号和业务凭据保持隔离；独立模式使用各自入口；
- `.env`、生产环境文件、私钥和 Token 文件权限为 `0600`，不提交 Git；
- 管理 API/MCP 不单独发布 TCP 端口；
- Grafana 匿名访问保持关闭，内部管理端口保持回环绑定；
- OTLP `4318` 当前是明文且无应用层认证，只能开放到可信网段并配置网络 ACL；
- 联合模式的适配器必须配置非空高强度 Bearer Token；共享 Nginx 的内部 listener 与适配器端口均不发布到宿主，预期调用方是管理 API，但同一容器网络内的连接仍必须通过 Token 认证；
- PostgreSQL reader、writer、migration owner 和 observability reader 必须是不同角色；
- 所有跨主机 PostgreSQL 连接使用 `sslmode=verify-full`，并通过受信 CA 校验数据库主机名；不得用 `sslmode=require` 或跳过证书校验代替；
- 不关闭 SSH host key 校验，不在清单、revision 或环境示例中保存 Gateway Token。

### 3.4 端口规划

| 端口 | 默认绑定 | 所属模块 | 说明 |
| --- | --- | --- | --- |
| `443` | `0.0.0.0` | 联合 | 唯一用户 HTTPS：Grafana、管理台与 MCP |
| `8443` | `0.0.0.0` | 仅管理 | 独立管理 HTTPS |
| `443` | `0.0.0.0` | 仅运维 | 独立 Grafana HTTPS |
| `4318` | `0.0.0.0` | 运维 | OpenClaw OTLP/HTTP |
| `8011` | 不发布 | 联合适配器 | 只在运维容器网络监听 |
| `8080` | 不发布 | 联合 Nginx | 管理网络到适配器的内部代理 |
| `3000`、`9090`、`9093`、`10902`、`9001`、`13133`、`8123`、`9002` | `127.0.0.1` | 运维 | 管理、查询或健康端口 |

联合模式不会启动 `8443`，也不会启动两个独立 Nginx。共享 Nginx 是唯一同时加入管理后端网络和运维默认网络的容器；管理 API、MCP 与适配器本身不直接加入对方网络。外部 PostgreSQL 属于独立数据库安全域；本机 PostgreSQL 则只加入独立 internal `agentswatch-database`，业务覆盖只把管理 API 和适配器接入该网络，MCP 与 Nginx 不接入。

## 4. 管理模块基础部署

两种模式都执行 4.1–4.3 的镜像、数据库和目录准备。“仅管理”继续执行 4.4–4.5；联合模式改按第 6 节用固定四文件序列初始化和启动。更完整的 UDS、Nginx 和测试直部署说明见[管理模块生产部署](../deploy/management/README.md)。

### 4.1 构建并发布镜像

在仓库根目录执行：

```bash
docker build \
  -f deploy/management/Dockerfile \
  -t registry.example.invalid/agentswatch-management:<version> \
  .
```

将镜像推送到部署环境可访问的受控 Registry，记录 digest。生产前端、API、MCP 和内置 Playbook 随同一个镜像版本发布。

### 4.2 准备 PostgreSQL

由数据库管理员创建数据库、migration owner 及管理连接策略。部署机创建临时迁移虚拟环境：

同机全部容器化部署先按[单机 PostgreSQL 说明](../deploy/postgres/README.md)完成独立数据库项目、TLS、HBA、无宿主端口验证和 application-network 覆盖。已有 PGDATA 转换拓扑时必须先做逻辑与停库物理备份，保持原 PostgreSQL 16 镜像，并保证任何时刻只有一个容器挂载该目录。

```bash
python3 -m venv .venv-deploy
.venv-deploy/bin/pip install -r requirements-api.txt
```

先安装数据库 CA：

```bash
sudo install -d -o root -g root -m 0755 /etc/agentswatch/database
sudo install -o root -g root -m 0444 <PostgreSQL-CA文件> /etc/agentswatch/database/ca.crt
```

以下宿主机命令使用该路径。所有 DSN 都必须使用证书 SAN 中的数据库主机名；URI 中的密码和特殊字符需正确进行 percent-encoding。

先只读查看迁移计划，再执行迁移：

```bash
AGENTSWATCH_MIGRATION_DATABASE_URL='<migration-dsn-from-secret-store>' \
  .venv-deploy/bin/python scripts/apply_core_asset_migrations.py --plan

AGENTSWATCH_MIGRATION_DATABASE_URL='<migration-dsn-from-secret-store>' \
  .venv-deploy/bin/python scripts/apply_core_asset_migrations.py
```

迁移器要求 PostgreSQL 16+，按版本校验历史 checksum，并自动排除只用于本机开发的 `003_local_reader.sql`。不要修改已应用的迁移文件。

创建并校验最小权限 reader/writer 角色：

```bash
AGENTSWATCH_DATABASE_ADMIN_URL='<database-admin-dsn-from-secret-store>' \
AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE='<migration-owner-role>' \
AGENTSWATCH_DATABASE_READER_ROLE='<reader-role>' \
AGENTSWATCH_DATABASE_READER_PASSWORD='<reader-password-at-least-16-chars>' \
AGENTSWATCH_DATABASE_WRITER_ROLE='<writer-role>' \
AGENTSWATCH_DATABASE_WRITER_PASSWORD='<writer-password-at-least-16-chars>' \
  .venv-deploy/bin/python scripts/configure_core_asset_roles.py
```

不要把真实 DSN 或密码直接保留在 shell history；生产应通过秘密管理工具注入上述环境变量。

### 4.3 准备目录、TLS 和环境文件

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /srv/agentswatch/management/automation
sudo install -d -o 10001 -g 10001 -m 0700 /srv/agentswatch/management/credentials
sudo install -d -o 10001 -g 10001 -m 0700 /srv/agentswatch/management/home
sudo cp deploy/management/.env.production.example /etc/agentswatch/management.production.env
sudo chmod 0600 /etc/agentswatch/management.production.env
```

仅管理模式另行创建 `/etc/agentswatch-management/tls`，并把组织证书与私钥放入：

```bash
sudo install -d -o root -g root -m 0700 /etc/agentswatch-management/tls
```

```text
/etc/agentswatch-management/tls/tls.crt
/etc/agentswatch-management/tls/tls.key
```

联合模式不创建第二套管理证书，统一使用第 5.1 节的 `nginx/certs`。填写 `/etc/agentswatch/management.production.env`：

- `AGENTSWATCH_MANAGEMENT_IMAGE`：已固定的管理镜像；
- `AGENTSWATCH_MANAGEMENT_DATA_HOST_ROOT`：宿主持久目录，默认 `/srv/agentswatch/management`，以只读镜像外的目录挂载到容器内 `/data/agentswatch/management`；必须与上一步 `install -d` 的路径一致；
- 仅管理模式把 `AGENTSWATCH_MANAGEMENT_NGINX_IMAGE` 固定到审核过的 tag 或 digest；联合模式不启动该镜像；
- `AGENTSWATCH_NGINX_GID`：活动 Nginx 镜像中 `nginx` 组的数字 GID，官方 alpine 镜像默认 `101`；
- 仅管理模式填写 `AGENTSWATCH_MANAGEMENT_BIND_ADDRESS`、`AGENTSWATCH_MANAGEMENT_HTTPS_PORT`、`AGENTSWATCH_MANAGEMENT_TLS_DIR`；联合模式这些值不用于活动入口；
- `AGENTSWATCH_DATABASE_CA_HOST_FILE=/etc/agentswatch/database/ca.crt`：只读挂载到需要数据库连接的生产容器固定 CA 路径；
- `AGENTSWATCH_DATABASE_URL`：reader DSN，包含 `sslmode=verify-full&sslrootcert=/run/secrets/agentswatch-database-ca.crt`；
- `AGENTSWATCH_WRITE_DATABASE_URL`：writer DSN，使用同样的 TLS 参数；
- `AGENTSWATCH_WEB_CREDENTIAL_KEY` 与 `AGENTSWATCH_SSH_CREDENTIAL_KEY`：两把独立 Fernet key；
- `AGENTSWATCH_CREDENTIAL_FILE_MAP`：凭据引用到容器内文件路径的 JSON；
- 仅管理模式把适配器 URL 与 Token 留空；联合模式由 combined 覆盖统一注入，不在此文件重复维护。

可在受控终端中生成 Fernet key：

```bash
.venv-deploy/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

两个用途分别生成一次，不要复用。私钥文件放在 `/srv/agentswatch/management/credentials` 并保持 `0600`。

### 4.4 仅管理模式：初始化并启动

先验证 Compose 变量：

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  config --quiet
```

首次部署初始化项目私有 UDS volume：

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add FOWNER --cap-add FSETID --entrypoint sh management-api \
  -c 'chown "10001:${AGENTSWATCH_NGINX_GID}" /data/agentswatch/management/run && chmod 2750 /data/agentswatch/management/run'
```

创建首个管理员。密码至少 12 位并同时包含大写字母、小写字母、数字和符号：

```bash
read -rsp 'Initial AgentsWatch admin password: ' AGENTSWATCH_INITIAL_PASSWORD; echo
printf '%s\n' "$AGENTSWATCH_INITIAL_PASSWORD" | docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  run --rm --no-deps --entrypoint agentswatch-manage-auth-user management-api \
  --username admin --display-name '系统管理员' --role admin --password-stdin
unset AGENTSWATCH_INITIAL_PASSWORD
```

启动三个管理服务：

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  up -d
```

### 4.5 仅管理模式：验收

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  ps

curl --fail --cacert <组织CA文件> \
  https://<管理域名>:8443/agentswatch/healthz
```

还应完成以下业务验收：

- 使用新管理员登录并立即创建独立日常账号；
- 分别验证 `viewer`、`operator`、`admin` 的服务端权限；
- 创建 API Key，调用只读 API 后撤销；
- 批量导入先预览后提交一份小型测试工作簿；
- 对一台测试服务器完成 host key 核验、首次纳管和只读采集任务；
- 从任务中心确认事件、输出脱敏和逐目标结果。

## 5. 运维模块基础部署

两种模式都执行 5.1 的运维环境、证书和存储准备。“仅运维”继续执行 5.2–5.4；联合模式改按第 6 节统一启动，并使用容器化适配器。离线环境的镜像导出和导入见[离线镜像包说明](../deploy/offline/README.md)。

### 5.1 准备环境与秘密

仅运维模式从开发示例复制本地环境文件：

```bash
cp .env.example .env
chmod 0600 .env
```

联合生产模式必须改用 fail-closed 模板，并把填充后的文件放在源码树外：

```bash
sudo install -o root -g root -m 0600 \
  deploy/combined/.env.operations.production.example \
  /etc/agentswatch/operations.production.env
```

至少修改：

- 所有镜像变量，固定版本或 digest；
- `AGENTSWATCH_DATA_ROOT`；
- Grafana、MinIO、ClickHouse 的账号与高强度密码；
- 指标、trace、log 和 ClickHouse 保留周期；
- `GRAFANA_PUBLIC_HOST`、`GRAFANA_PUBLIC_URL`；
- 端口绑定和网络 ACL；
- `AGENTSWATCH_OBSERVABILITY_CONTROL_UID/GID`，必须等于实际写 revision 的专用进程账号数字 UID/GID。

把组织证书放到 `nginx/certs/tls.crt` 和 `nginx/certs/tls.key`，私钥保持 `0600`。如果证书不存在，初始化服务会生成自签证书；该行为只用于首次验收，不是生产信任方案。

默认 `ADMIN_BIND_ADDRESS=127.0.0.1` 应保持不变。`OTEL_BIND_ADDRESS=0.0.0.0` 时，必须在主机或网络防火墙上只允许批准的 OpenClaw 网段访问 `4318`。

### 5.2 仅运维模式：启动数据栈

```bash
docker compose --env-file .env -f docker-compose.yml config --quiet
docker compose --env-file .env -f docker-compose.yml up -d
docker compose --env-file .env -f docker-compose.yml ps --all
```

`storage-init` 会创建并设置运维数据目录权限，Prometheus 会先加载空的 bootstrap revision。不要手工把 `observability-control` 目录改成 world-writable。

### 5.3 仅运维模式：验证后端

```bash
docker compose --env-file .env -f docker-compose.yml \
  exec -T prometheus promtool check config /etc/prometheus/prometheus.yml

docker compose --env-file .env -f docker-compose.yml \
  exec -T prometheus promtool check rules /etc/prometheus/alerts/openclaw.rules.yml

docker compose --env-file .env -f docker-compose.yml \
  exec -T otel-collector \
  /otelcol-contrib validate --config /etc/otelcol-contrib/config.yaml

curl -fsS http://127.0.0.1:13133/
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:10902/-/ready
curl -kfsS https://127.0.0.1/nginx-health
./scripts/check_persistence.sh
```

`curl -k` 只用于本机检查自签或内部证书；用户访问必须校验证书链。

### 5.4 仅运维模式：安装适配器运行环境

```bash
python3 -m venv .venv-observability-control
.venv-observability-control/bin/pip install \
  -r requirements-observability-control.txt
```

适配器状态根目录必须由 `.env` 中配置的 UID/GID 对应账号写入。所有 CLI 和服务命令都应使用这个专用账号执行。

## 6. 模式一：管理模块和运维模块同时部署

联合模式必须使用 `deploy/combined` 覆盖，不得分别启动两个基础 Compose。最终只有 `combined-nginx` 提供用户 HTTPS：

- `https://<统一域名>/`：Grafana；
- `https://<统一域名>/agentswatch/`：管理台与 REST API；
- `https://<统一域名>/agentswatch-mcp/mcp`：MCP；
- `combined-nginx:8080`：只存在于容器网络，供管理 API 访问适配器。

### 6.1 准备镜像、数据库和环境文件

先完成 4.1–4.3 和 5.1，构建并发布适配器镜像：

```bash
docker build \
  -f deploy/combined/Dockerfile.observability-control \
  -t registry.example.invalid/agentswatch-observability-control:<version> \
  .
```

适配器使用专用最小镜像，不复用管理镜像。迁移 `020_observability_gateway_v2.sql` 必须已应用，再创建与管理 reader/writer 不同的只读角色：

```bash
AGENTSWATCH_DATABASE_ADMIN_URL='<database-admin-dsn-from-secret-store>' \
AGENTSWATCH_DATABASE_MIGRATION_OWNER_ROLE='<migration-owner-role>' \
AGENTSWATCH_OBSERVABILITY_DATABASE_ROLE='<observability-reader-role>' \
AGENTSWATCH_OBSERVABILITY_DATABASE_PASSWORD='<independent-strong-password>' \
  .venv-deploy/bin/python scripts/configure_observability_integration_role.py
```

脚本会验证该角色只拥有 v2 视图的必要读取权限，并拒绝多余关系、列、序列或可利用的 `SECURITY DEFINER` 权限。

复制联合环境模板：

```bash
sudo cp deploy/combined/.env.production.example /etc/agentswatch/combined.production.env
sudo chmod 0600 /etc/agentswatch/combined.production.env
```

保持模板中的 `COMPOSE_PROFILES=` 为空，并填写四个业务变量：

```dotenv
AGENTSWATCH_OBSERVABILITY_CONTROL_IMAGE=registry.example.invalid/agentswatch-observability-control:<version>
AGENTSWATCH_OBSERVABILITY_INTEGRATION_DATABASE_URL=<observability-reader-dsn-from-secret-store>
AGENTSWATCH_OTEL_ENDPOINT=http://<运维节点>:4318
AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN=<独立高强度Bearer令牌>
```

`AGENTSWATCH_OTEL_ENDPOINT` 会写入 Gateway 补丁，必须是 Gateway 实际可达的地址，不能填容器服务名。管理生产 env 中的适配器 URL/Token保持空白；联合覆盖会把 URL 固定为 `http://combined-nginx:8080`，并从联合 env 注入 Token。

联合模式复用 `nginx/certs/tls.crt` 与 `tls.key`，证书 SAN 应覆盖统一域名。`/etc/agentswatch/operations.production.env` 中：

- `HTTPS_PORT=443`；
- `GRAFANA_PUBLIC_URL=https://<统一域名>/`；
- `AGENTSWATCH_OBSERVABILITY_CONTROL_UID/GID` 与适配器容器实际用户一致；默认 `10001:10001`，若修改，`storage-init` 与适配器会使用同一组值。

### 6.2 统一检查与初始化

以下命令中的文件顺序和项目名不可改变；所有相对 bind path 都以仓库根目录的第一个 Compose 文件为基准：

先显式清空 profile。该 shell 环境变量优先于任何历史 `.env` 配置，可避免误启 `nginx` 或 `management-nginx` 与共享入口抢占 `443`：

```bash
export COMPOSE_PROFILES=
```

```bash
docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  config --quiet
```

`config --quiet` 只检查插值与结构。进入维护窗口前还要用完全相同的项目、env 和 Compose 顺序展开 JSON，并直接通过 stdin 交给生产预检；展开内容含生产秘密，禁止使用 `tee`、shell trace 或普通日志保存：

```bash
AGENTSWATCH_RELEASE_RECORD_DIR=/srv/agentswatch/release-records/$(date -u +%Y%m%dT%H%M%SZ)
sudo install -d -o root -g root -m 0700 "$AGENTSWATCH_RELEASE_RECORD_DIR"

docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  config --format json \
  | sudo python3 scripts/validate_production_release.py \
      --image-record "$AGENTSWATCH_RELEASE_RECORD_DIR/application-images.json"
```

预检会拒绝空白、示例或浮动镜像、已知弱口令、错误的公开端口、误启的 standalone ingress、不安全的数据库 DSN、危险 inventory override 和数据库网络漂移，并以 mode `0600` 记录本机实际 image ID。image record 必须是新文件；预检失败不得执行后续初始化或启动。使用本机独立 PostgreSQL 或 legacy relay 时，上述两个检查命令也必须在相同位置追加对应覆盖，不能只在 `up` 时临时加入。

首次部署初始化联合项目的私有 UDS volume：

```bash
docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add FOWNER --cap-add FSETID --entrypoint sh management-api \
  -c 'chown "10001:${AGENTSWATCH_NGINX_GID}" /data/agentswatch/management/run && chmod 2750 /data/agentswatch/management/run'
```

创建首个管理员也必须使用同一项目和四文件组合：

```bash
read -rsp 'Initial AgentsWatch admin password: ' AGENTSWATCH_INITIAL_PASSWORD; echo
printf '%s\n' "$AGENTSWATCH_INITIAL_PASSWORD" | docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  run --rm --no-deps --entrypoint agentswatch-manage-auth-user management-api \
  --username admin --display-name '系统管理员' --role admin --password-stdin
unset AGENTSWATCH_INITIAL_PASSWORD
```

### 6.3 启动统一栈并激活清单

```bash
docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  up -d --pull never
```

`COMPOSE_PROFILES` 必须保持为空，且命令不得增加 `--profile standalone-ingress`，因此 `nginx` 与 `management-nginx` 不会启动。等待服务健康后，在适配器容器中预览：

```bash
docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  exec -T observability-control \
  python -m agentswatch_observability preview --source management-db
```

确认预览中的 `activation_allowed=true`、`activation_blockers=[]`，并核对 `total`、`deployable`、`disable` 与 `orphan` 数量符合审批基线。适配器默认拒绝空清单、没有任何可部署 Runtime 的清单，或移除任何此前可部署 Runtime 的清单，防止数据库导入或身份映射异常清空、缩减现有监控 revision。生产必须保持 `AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY=false` 和 `AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY=false`；合法移除只在审批窗口短时启用后者，完整下线必须同时启用两项，操作后立即全部恢复为 `false`。

确认差异后，把预览返回的哈希用于同步：

```bash
docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  exec -T observability-control \
  python -m agentswatch_observability sync \
    --source management-db \
    --expected-snapshot-hash <preview返回的snapshot_hash>
```

适配器从运维网络调用 `http://prometheus:9090/-/reload`。管理 API 则通过共享 Nginx 内部 `8080` 转发到适配器，二者都不需要发布适配器端口。

### 6.4 下发 Gateway 配置

适配器生成的补丁位于：

```text
/srv/agentswatch/observability-control/current/openclaw-monitoring-patches/
```

先对单台灰度目标只读预检，再显式应用：

```bash
./scripts/apply_lobster_config.sh <gateway-id> \
  --patch-dir /srv/agentswatch/observability-control/current/openclaw-monitoring-patches \
  --ssh <user>@<host> \
  --known-hosts /etc/agentswatch/ssh_known_hosts \
  --identity-file /etc/agentswatch/keys/management_ed25519 \
  --dry-run

./scripts/apply_lobster_config.sh <gateway-id> \
  --patch-dir /srv/agentswatch/observability-control/current/openclaw-monitoring-patches \
  --ssh <user>@<host> \
  --known-hosts /etc/agentswatch/ssh_known_hosts \
  --identity-file /etc/agentswatch/keys/management_ed25519
```

完成灰度遥测验收后再分批扩大。生成 revision 不会自动执行这一步。

### 6.5 联合验收

- `test -z "${COMPOSE_PROFILES:-}"` 成功，`docker compose ... ps --all` 只显示 `combined-nginx`，不显示活动的 `nginx` 或 `management-nginx`；
- `https://<统一域名>/nginx-health`、`/`、`/agentswatch/healthz` 分别可访问；
- `observability-control` 与 `8011` 没有宿主 published port；
- 管理 API/MCP 只加入 `management-backend`，适配器只加入运维默认网络，共享 Nginx 同时加入二者；
- 适配器状态显示非 bootstrap 活动 revision，Prometheus reload 成功；
- 管理页能加载差异预览、确认同步并显示 Job 结果；
- 停止适配器容器后，管理资产页继续可用，运维侧仍保留最后 revision；
- 暂停管理 API 后，Prometheus、Grafana 和既有采集继续工作；
- 停止共享 Nginx 时，两个用户界面同时失去入口，但后端采集和存储继续运行；
- 单 Gateway 验收 OTel 主链路：

```bash
./scripts/check_openclaw_telemetry.py \
  --prometheus-url http://127.0.0.1:9090 \
  --gateway-id <gateway-id> \
  --skip-native
```

如已在 Overlay 中显式开启原生 Prometheus，并安全配置独立 Token，再增加 Gateway URL 验证并移除 `--skip-native`。两条指标链路只并行核验，不相加。

## 7. 模式二：只部署管理模块

执行第 4 节的全部步骤，不执行运维模块和适配器步骤。

在 `/etc/agentswatch/management.production.env` 中明确留空：

```dotenv
AGENTSWATCH_OBSERVABILITY_CONTROL_URL=
AGENTSWATCH_OBSERVABILITY_CONTROL_TOKEN=
```

此模式下可用：

- 服务器、运行时、智能体、组织和管理分组；
- Excel 批量预览与入库；
- SSH 首次纳管、自动化库、任务中心；
- 用户权限、API Key、REST API 与 MCP；
- Runtime WebUI 的受控访问方式。

不可用的是运维遥测、Grafana 和清单同步。“可观测接入”页显示未配置是正确降级状态，不应当作管理服务故障。

验收除第 4.5 节外，还应确认：

- 没有任何管理容器加入运维网络或挂载运维目录；
- 管理 API 日志不会反复尝试未配置的适配器；
- 资产、导入和任务不依赖 Grafana、Prometheus 或 OTLP 服务。

## 8. 模式三：只部署运维模块

执行第 5 节的全部步骤，不部署 PostgreSQL 和管理 Compose。适配器使用本地 JSON 兼容清单。

### 8.1 同步本地清单

维护 `cluster/lobsters.json`，字段和 Token 处理见[本地 JSON 清单](../cluster/README.md)。在适配器专用账号的环境中配置：

```bash
export AGENTSWATCH_OBSERVABILITY_CONTROL_ROOT=/srv/agentswatch/observability-control
export AGENTSWATCH_OTEL_ENDPOINT=http://<运维节点>:4318
export AGENTSWATCH_PROMETHEUS_RELOAD_URL=http://127.0.0.1:9090/-/reload
```

预览、同步并检查状态：

```bash
.venv-observability-control/bin/python scripts/observabilityctl.py preview \
  --source local-json \
  --inventory cluster/lobsters.json

.venv-observability-control/bin/python scripts/observabilityctl.py sync \
  --source local-json \
  --inventory cluster/lobsters.json \
  --expected-snapshot-hash <preview返回的snapshot_hash>

.venv-observability-control/bin/python scripts/observabilityctl.py status
```

此模式无需长期启动适配器 HTTP API；每次清单或 Overlay 变更后运行 preview/sync 即可。若需要远程自动化控制，按[适配器专项手册](../agentswatch_observability/README.md)使用 supervisor 启动 API，并配置非空 Token、独立 HTTPS 入口和来源 ACL。

### 8.2 配置 Gateway

使用第 6.4 节相同的 `--patch-dir` 对单台灰度目标预检并下发。OpenClaw 必须安装官方 `diagnostics-otel` 与 `diagnostics-prometheus` 插件；原生 Prometheus 默认不启用，OTel 主链路不需要 Gateway Prometheus Token。

只有在 Overlay 显式启用 `native_scrape_enabled` 并满足目标就绪条件时，才为对应 Gateway 准备：

```text
prometheus/secrets/gateways/<gateway-id>.token
```

Token 文件权限必须允许 Prometheus 组只读且禁止其他用户读取。当前管理数据库模式没有自动分发这些 Token 的能力；生产需使用现有 `lobsterctl token-fetch` 或组织秘密分发流程。

### 8.3 运维独立验收

- 所有后端通过第 5.3 节健康检查；
- `observabilityctl status` 显示非 bootstrap 活动 revision；
- Prometheus 配置中的动态 `current/openclaw-gateways.yml` 可加载；
- Gateway 产生真实调用后，OTel 指标、trace 和 log 均可查询；
- Grafana 使用组织证书登录，匿名访问关闭；
- 如果启用原生抓取，单独验证 `job="openclaw-gateways"` 的 `up`，不与 OTel 运营指标相加。

## 9. 停止与重新启动

停止不会删除数据卷或持久目录。

### 管理模块

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  down
```

重新启动使用第 4.4 节的 `up -d`。不要加 `-v`，否则会删除项目私有 UDS volume；即使误删，重新启动前也必须再次执行 UDS 初始化。

### 运维模块

```bash
docker compose --env-file .env -f docker-compose.yml down
```

联合模式使用同一组四文件和项目名整体停止：

```bash
export COMPOSE_PROFILES=
docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  down
```

重新启动仍使用第 6.3 节的四文件命令。不要加 `-v`，也不要删除 `/srv/agentswatch` 或 `prometheus/secrets`。

## 10. 备份、升级与回滚

### 10.1 必须备份的内容

| 模块 | 备份对象 |
| --- | --- |
| 管理 | PostgreSQL 一致性备份、`/srv/agentswatch/management`、生产 env、Fernet key、SSH 私钥、管理 TLS |
| 运维 | 独立模式 `.env` 或联合模式 `operations.production.env`、运维 TLS、Prometheus Gateway Token、`observability-control` revision/Overlay、MinIO、ClickHouse 和各本地状态目录 |
| 联合 | 上述全部、联合 env 与统一入口 TLS，并记录管理迁移版本和当前活动 revision |

秘密备份必须加密，恢复演练应在隔离环境进行。

### 10.2 升级顺序

1. 记录当前镜像 digest、Git commit、迁移版本和活动 revision；
2. 完成数据库与数据目录备份；
3. 使用 `--plan` 查看数据库迁移；
4. 先执行兼容的新迁移，再发布匹配的管理镜像；
5. 分组件更新运维镜像，执行配置校验和后端健康检查；
6. 重新运行适配器 preview，确认差异后同步；
7. 对单 Gateway 和关键管理流程做回归；
8. 观察稳定后再清理旧镜像和超出保留策略的备份。

### 10.3 回滚原则

- 管理镜像只能回滚到兼容当前数据库 Schema 的版本；仓库没有自动 down migration。
- 不兼容的数据库回滚必须恢复升级前的一致性备份，并同步恢复匹配的管理镜像和加密密钥。
- 运维适配器配置优先使用 `.venv-observability-control/bin/python scripts/observabilityctl.py rollback <revision_id>`；联合模式在适配器容器内执行对应 `python -m agentswatch_observability rollback`，不要手工修改 `current` 链接内文件。
- 运维镜像回滚使用已记录 digest，并在启动后重新执行 Prometheus、Collector、Loki、Tempo 和持久化校验。
- Gateway 配置下发脚本会为单次变更保留备份并在失败时恢复，但这不能代替平台级备份。

## 11. 上线验收清单

- [ ] 已明确选择一种部署模式；联合模式使用固定四文件序列和项目名，独立模式未误加载 combined 或 lockdown。
- [ ] 所有镜像已固定版本或 digest。
- [ ] 联合生产 Compose 已通过 `validate_production_release.py`，受保护的 image ID 记录已归档。
- [ ] 生产密码、Token、DSN、Fernet key、私钥未进入 Git。
- [ ] 联合模式只有一个用户 HTTPS/443 Nginx，后端网络分离；独立模式只启动各自 Nginx。
- [ ] 联合命令执行环境的 `COMPOSE_PROFILES` 为空，未启用 `standalone-ingress`。
- [ ] 活动 Nginx 的 worker GID 与 `AGENTSWATCH_NGINX_GID`、管理 UDS 目录 GID 一致。
- [ ] PostgreSQL 16+ 迁移计划、应用结果和角色权限均已验证。
- [ ] 所有外部 PostgreSQL DSN 使用 `sslmode=verify-full`，数据库主机名和受信 CA 校验通过。
- [ ] 管理 API/MCP 没有发布独立 TCP 端口。
- [ ] OTLP `4318` 仅对批准网段开放。
- [ ] 联合适配器配置非空 Token，适配器和共享 Nginx 内部 listener 均无宿主端口，管理调用已通过该 listener 与 Token 验证。
- [ ] Prometheus 原生抓取仅在显式启用的目标上配置独立 Token。
- [ ] OTel 指标与原生 Prometheus 指标未相加。
- [ ] 监控清单已从正确事实源生成并激活非 bootstrap revision。
- [ ] 补丁下发完成单台灰度、回滚和真实遥测验收。
- [ ] Alertmanager 已按组织要求配置外部通知；默认空 receiver 不算生产通知闭环。
- [ ] PostgreSQL、管理数据、运维数据、revision、TLS 和秘密均已完成备份恢复演练。

## 12. 进一步阅读

- [管理模块生产部署](../deploy/management/README.md)
- [联合部署覆盖](../deploy/combined/README.md)
- [运维控制适配器](../agentswatch_observability/README.md)
- [离线镜像包](../deploy/offline/README.md)
- [遥测数据持久化](data-persistence.md)
- [Grafana 看板与运营报表](grafana-dashboards.md)
- [本地 JSON 清单](../cluster/README.md)
