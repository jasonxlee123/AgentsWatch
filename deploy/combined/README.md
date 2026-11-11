# 管理与运维联合部署覆盖

本目录只用于“管理模块 + 运维模块”联合部署。它把根运维 Compose 与管理生产 Compose 合并为同一个项目，禁用两套独立入口，并启用一个共享 `combined-nginx`：

- 唯一用户 HTTPS 入口为 `:443`；OTLP `:4318` 仍单独向批准的 Gateway 网段开放，管理端口只绑定回环；
- `/` 与 `/api/live/` 转发 Grafana；
- `/agentswatch/` 转发管理 API Unix socket；
- `/agentswatch-mcp/` 转发 MCP Unix socket；
- 未发布的内部 `:8080` 把管理 API 请求转发到运维适配器；它不是身份边界，所有控制请求仍由适配器强制校验 Bearer Token。

运维后端保留默认网络，管理 API/MCP 使用 `management-backend`，共享 Nginx 同时加入两个网络。除共享入口外，管理服务不直接加入运维网络；适配器不发布宿主端口。

执行任何联合 Compose 命令前都要设置 `COMPOSE_PROFILES=`，不得启用 `standalone-ingress`；否则两套独立入口会被重新加入项目并与共享 Nginx 争用端口。活动 Nginx 镜像的 worker GID 还必须与管理 env 中的 `AGENTSWATCH_NGINX_GID` 一致。管理 API 与适配器访问生产 PostgreSQL 时，Compose 会只读挂载 `AGENTSWATCH_DATABASE_CA_HOST_FILE`，并强制使用 `verify-full` 与该 CA 校验数据库身份。

生产环境还必须在三份基础业务 Compose 后追加 `compose.production-lockdown.yml`。该覆盖要求所有上游镜像、MinIO、ClickHouse 和 Grafana 凭据显式提供，并为所有生产容器设置 `pull_policy: never`，从而避免根 Compose 的本地开发默认值或浮动镜像静默进入生产。运维环境从 `.env.operations.production.example` 复制到源码树外的 root `0600` 文件；不得使用 `:latest`、空值、示例值或已知开发弱口令。维护窗口前必须把相同顺序的 `docker compose config --format json` 直接通过 stdin 交给 `scripts/validate_production_release.py`，并把其生成的 mode `0600` 本地 image ID 记录归档；不得把含秘密的展开 JSON 写入普通文件或日志。

同机数据库使用独立 `deploy/postgres/compose.production.yml` 项目，并在 lockdown 后追加 `deploy/postgres/compose.application-network.yml`。若保留旧 `/openclaw/` 路由，再把 `compose.legacy-openclaw-relay.yml` 放在最后。所有 `config`、初始化、`up`、`exec` 和 `down` 命令必须保持相同项目名、env 顺序和以下 Compose 顺序：

1. 根目录 `docker-compose.yml`
2. `deploy/management/compose.production.example.yml`
3. `deploy/combined/compose.production.example.yml`
4. `deploy/combined/compose.production-lockdown.yml`
5. `deploy/postgres/compose.application-network.yml`
6. 可选 `deploy/combined/compose.legacy-openclaw-relay.yml`

适配器默认拒绝激活空清单、全部阻断的清单，或移除任何此前可部署 Runtime 的清单，避免数据库导入或身份映射异常清空、缩减现有监控 revision。`AGENTSWATCH_OBSERVABILITY_ALLOW_EMPTY_INVENTORY` 与 `AGENTSWATCH_OBSERVABILITY_ALLOW_DESTRUCTIVE_INVENTORY` 必须保持 `false`；经过审批的合法移除只短时启用后者，完整可观测性下线必须同时启用两项，操作后立即全部恢复为 `false`。

如确有兼容需求，可在上述必需覆盖之后最后追加 `compose.legacy-openclaw-relay.yml`。该覆盖用非 root、只读且无 capability 的 host-network 容器把项目私有 Unix socket 转发到宿主 `127.0.0.1:16402`，共享 Nginx 继续使用 htpasswd 保护兼容路由。不要直接 bind owner/group 未知的旧文件；先复制到专用 root-owned 文件，把 group 设置为活动 Nginx worker 的数字 GID、mode 设置为 `0640`，验证 worker 可读后再填写 `AGENTSWATCH_LEGACY_OPENCLAW_HTPASSWD_FILE`。没有明确兼容需求时不得启用该覆盖；确认无访问后应单独移除。

完整准备、迁移、启动与验收步骤见[统一部署指引](../../docs/deployment-guide.md)。
