# AgentsWatch 单机生产 PostgreSQL

本目录用于必须把 PostgreSQL 与 AgentsWatch 部署在同一台服务器、但仍要求全部服务容器化的场景。数据库使用独立 Compose 项目、独立 internal Docker 网络和 TLS `verify-full`；它不发布宿主端口，也不随业务栈的 `down` 停止。

## 边界

- PostgreSQL 必须为 16 或更高版本；升级现有站点时先保持当前镜像 ID，不要同时升级数据库小版本。
- 数据位于 `/srv/agentswatch/management/postgres`。任何时刻只能有一个 PostgreSQL 容器挂载该目录。
- `agentswatch-database` 仅连接 PostgreSQL、`management-api` 和 `observability-control`。MCP、Nginx 与其他运维容器不得加入。
- 客户端固定使用主机名 `agentswatch-db`；服务端证书 SAN 只签发该 DNS 名。
- 本方案仍是单机数据库，不具备高可用能力。上线前必须完成逻辑备份、停库物理备份与恢复校验。

## 准备

复制环境文件并保留现有数据库管理员密码：

```bash
sudo install -d -o root -g root -m 0700 /etc/agentswatch/postgres
sudo install -o root -g root -m 0600 deploy/postgres/.env.production.example \
  /etc/agentswatch/postgres.production.env
sudo install -o root -g root -m 0600 /path/to/existing-admin-password \
  /etc/agentswatch/postgres/admin-password
sudo install -o root -g root -m 0644 deploy/postgres/pg_hba.conf \
  /etc/agentswatch/postgres/pg_hba.conf
```

确认 `AGENTSWATCH_DATABASE_NETWORK_SUBNET` 不与现有主机或 Docker 网络重叠，并把 `AGENTSWATCH_POSTGRES_IMAGE` 固定为服务器当前已验证的 PostgreSQL 16 镜像。已有 PGDATA 时，管理员用户名和数据库名必须与现状一致。
模板故意把 `AGENTSWATCH_POSTGRES_ADMIN_USER` 留空并由 Compose 强制要求填写，避免已有 PGDATA 被一个无效的通用默认角色误导；现场应填写实际数据库 owner/migration owner。

生成内部 CA 与数据库证书。脚本不会覆盖已有或异常的证书材料；复用时还会校验证书与私钥配对、SAN、链、有效期以及 owner/group/mode：

```bash
sudo deploy/postgres/prepare_tls.sh
```

## 从测试项目无损切换

1. 在线执行 `pg_dumpall --globals-only` 和 `pg_dump -Fc`，校验备份可列出。
2. 停止所有数据库写入方，再正常停止旧 PostgreSQL 容器。
3. 对 PGDATA 做保留 UID/GID/xattr 的离线备份；禁止 `down -v`。
4. 使用当前相同镜像启动独立生产数据库项目：

```bash
docker compose \
  --env-file /etc/agentswatch/postgres.production.env \
  -f deploy/postgres/compose.production.yml \
  up -d
```

5. 确认 PostgreSQL 没有 published port，并在 `agentswatch-database` 网络中的一次性客户端验证：正确 CA + `verify-full` 成功，`sslmode=disable` 和错误主机名失败。
6. 在任何 schema 迁移前再次生成逻辑备份，然后用版本化源码的迁移脚本先 `--plan`、再应用到目标版本。

联合业务栈先在三份基础业务 Compose 后追加生产 lockdown，再追加数据库网络覆盖：

```bash
docker compose -p agentswatch-full \
  --env-file /etc/agentswatch/operations.production.env \
  --env-file /etc/agentswatch/management.production.env \
  --env-file /etc/agentswatch/combined.production.env \
  -f docker-compose.yml \
  -f deploy/management/compose.production.example.yml \
  -f deploy/combined/compose.production.example.yml \
  -f deploy/combined/compose.production-lockdown.yml \
  -f deploy/postgres/compose.application-network.yml \
  config --quiet
```

`config --quiet` 只验证 Compose 插值和结构。进入维护窗口前，还必须用完全相同的项目、env 和文件顺序执行 `config --format json`，只通过 stdin 交给 `scripts/validate_production_release.py`；不要把包含生产凭据的展开 JSON 写入普通文件或日志。预检必须成功，并在受保护目录创建 mode `0600` 的本地镜像 ID 记录，随后所有 `run`、`up`、`exec` 和 `down` 命令继续保持上述顺序。

管理 reader、writer 与可观测只读 DSN 均从秘密管理系统注入，并且必须使用 `sslmode=verify-full` 与容器内 CA 路径，例如：

```text
<database-dsn-from-secret-store>
```

迁移后若需回滚旧管理版本，不能只切应用镜像；必须停止写入并恢复迁移前数据库备份。
