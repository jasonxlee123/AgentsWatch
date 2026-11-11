# AgentsWatch 管理模块生产部署

> 本文是管理模块的实现级专项手册。三种部署方式的选择、跨模块启动顺序与统一验收请先阅读[部署指引](../../docs/deployment-guide.md)。
> 本文中的 `management-nginx`、`8443` 和独立 TLS 目录只适用于“仅管理”模式；联合模式由 [`deploy/combined`](../combined/README.md) 禁用该入口并接入共享 Nginx/443。

## 部署边界

- 当前本机/测试环境允许直接部署 Python 服务，使用 systemd 单元。
- 生产环境必须使用多阶段容器镜像。
- 管理 API 固定为单 worker，并且测试、生产都只监听 `/srv/agentswatch/management/run/api.sock`。
- MCP 是独立的无状态 REST 适配进程，只监听 `/srv/agentswatch/management/run/mcp.sock`；它不连接数据库、Ansible 或 Shell。
- 生产复用外部 PostgreSQL；仅管理生产模式的入口使用管理模块自己部署的 Nginx，联合模式使用共享 Nginx。当前测试主机经核查没有 PostgreSQL，因此另提供一个隔离的 PostgreSQL 16 测试 Compose；它不加入运维网络。
- API 与 MCP 不发布 TCP 端口；仅管理模式由管理 Nginx 发布 HTTPS，联合模式由共享 Nginx 挂载同一项目 run volume。
- 前端由 API 从 `AGENTSWATCH_STATIC_DIR` 提供，Nginx 不直接读取前端目录。
- 持久数据统一位于 `/srv/agentswatch/management`。源码、镜像和示例文件中均不保存真实凭据。Compose 通过 `AGENTSWATCH_MANAGEMENT_DATA_HOST_ROOT`（默认 `/srv/agentswatch/management`）把宿主目录挂载到容器内 `/data/agentswatch/management`；宿主路径与本文件中的 `install -d` 命令必须一致。
- 联合模式只共享顶层 Compose 编排和 Nginx：管理/运维后端网络、数据目录与凭据保持隔离，共享 Nginx 是唯一跨网代理。独立模式不共享入口。

单 worker 是当前的安全约束：API 启动时会恢复排队任务，并在进程内创建 Ansible 执行线程。改成多 worker 前，必须先把任务认领和执行迁移到独立 worker。

`--forwarded-allow-ips=*` 只适用于这套 UDS 结构：API 没有 TCP 监听和 published port，请求只能从有权连接 socket 的进程进入。若以后增加 TCP 监听，必须改成明确的可信代理地址。

## 管理模块内部的 UDS 与专属 Nginx

官方 `nginx:stable-alpine` 的 Nginx 用户数字 GID 为 `101`。测试直部署使用宿主目录，需预创建 setgid run 目录：

```bash
sudo install -d -o 10001 -g 10001 -m 0755 /srv/agentswatch/management
sudo install -d -o 10001 -g 101 -m 2750 /srv/agentswatch/management/run
```

API 与 MCP 进程以目录 owner UID `10001` 的权限写入，不加入宿主 GID `101`（该 GID 在当前宿主属于 `systemd-journal`，加入会扩大日志读取权限）。两个 socket launcher 分别绑定 UDS、明确设置 `0660`，再把已绑定 FD 交给单 worker Uvicorn；socket 因 setgid 目录自动继承 GID `101`。不能只依赖 umask，因为 Uvicorn 的 `--uds` 会显式设置 `0666`。若管理专属 Nginx 镜像不是 `nginx:stable-alpine`，先确认其 worker GID，再同步修改目录 GID 和 `AGENTSWATCH_NGINX_GID`；仍不得把服务进程加入该组。

生产容器使用 Compose 项目私有的 `management-run` volume 覆盖同一路径，避免 Docker Desktop 在 host bind mount 上对 UDS 执行 chmod 时返回 `EINVAL`。该 volume 只挂载到 `management-api`、`management-mcp` 和当前部署模式的官方入口 Nginx，不得声明为 external，也不得挂到其他运维服务：

- 测试直部署：管理专属 Nginx 使用宿主 bind mount `/srv/agentswatch/management/run:/data/agentswatch/management/run:ro`。
- 生产容器：API、MCP 与当前入口共享项目私有 `management-run`，Nginx 侧只读。

只读挂载不妨碍 Nginx 连接已有 socket，也不会允许它创建或删除 socket。`nginx-agentswatch.conf.example` 是仅管理模式的完整 HTTPS `server`；联合模式必须改用 `deploy/combined/nginx-combined.conf`，不要手工合并配置。MCP Streamable HTTP 的外部地址为 `/agentswatch-mcp/mcp`，Nginx 会保留 `Authorization` 并关闭响应缓冲。管理台自己的账号密码、滑块验证和角色权限仍由 API 强制执行。

## 生产容器部署

### 1. 构建镜像

在仓库根目录执行：

```bash
docker build \
  -f deploy/management/Dockerfile \
  -t registry.example.invalid/agentswatch-management:VERSION \
  .
```

前端默认以 `/agentswatch/` 为基础路径构建，保存在镜像的 `/opt/agentswatch/frontend-dist`，API 通过 `AGENTSWATCH_STATIC_DIR` 提供。若需要显式传入路径，可增加 `--build-arg AGENTSWATCH_FRONTEND_BASE=/agentswatch/`。

Dockerfile 专用忽略文件采用白名单，只把 API、自动化代码、内置 Playbook 和前端源码送入构建；本机 `automation/artifacts`、inventory、runner 工作区、受管草稿及环境文件不会进入构建上下文。

### 2. 准备持久目录和配置

镜像内 API/MCP 用户为 UID/GID `10001`，不加入宿主 Nginx GID。首次部署需创建管理模块自己的持久目录；仅管理模式还需独立 TLS 目录：

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /srv/agentswatch/management/automation
sudo install -d -o 10001 -g 10001 -m 0700 /srv/agentswatch/management/credentials
sudo install -d -o 10001 -g 10001 -m 0700 /srv/agentswatch/management/home
sudo install -d -o root -g root -m 0755 /etc/agentswatch/database
sudo install -o root -g root -m 0444 <PostgreSQL-CA文件> /etc/agentswatch/database/ca.crt
sudo install -d -o root -g root -m 0700 /etc/agentswatch-management/tls
```

仅管理模式将组织签发的证书和私钥分别放到 `tls.crt`、`tls.key`，私钥保持 `0600`。联合模式使用统一入口的 `nginx/certs`，不启动管理 Nginx。

将 `.env.production.example` 复制到源码目录之外（例如 `/etc/agentswatch/management.production.env`），权限设为 `0600` 后填写：

- 管理应用镜像、仅管理 Nginx 镜像的固定 tag/digest、HTTPS 监听地址/端口和上述 TLS 目录；
- 活动 Nginx 镜像的 worker GID；默认官方 alpine 镜像对应 `AGENTSWATCH_NGINX_GID=101`；
- PostgreSQL CA 的宿主路径，以及包含 `sslmode=verify-full&sslrootcert=/run/secrets/agentswatch-database-ca.crt` 的只读和写入 DSN；
- 部署时生成的 WebUI Fernet 密钥；
- SSH `credential_ref` 到私钥文件的 JSON 映射；
- 独立的 `AGENTSWATCH_SSH_CREDENTIAL_KEY`，用于加密长期服务器登录密码；
- 仅管理模式把运维适配器 URL/Token 留空；联合模式由 combined 覆盖注入内部 Nginx URL 和 Bearer Token。

Compose 将 `AGENTSWATCH_DATABASE_CA_HOST_FILE` 只读挂载到上述容器内路径，并设置 libpq 的 `PGSSLMODE=verify-full`。数据库 DSN 必须使用证书 SAN 中的主机名，不能用跳过校验的 IP 或 `sslmode=require`；企业私有 CA 直接提供为 PEM 文件即可，无需修改镜像内系统信任库。

创建并初始化 Compose 项目私有的 UDS volume。初始化只做一次，必须使用刚构建的同版本镜像；不要用 `docker volume create` 建成可被其他 Compose 复用的 external volume：

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add FOWNER --cap-add FSETID --entrypoint sh management-api \
  -c 'chown "10001:${AGENTSWATCH_NGINX_GID}" /data/agentswatch/management/run && chmod 2750 /data/agentswatch/management/run'
```

生成 Fernet 密钥的示例命令如下。生成值只写入受限环境文件：

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

私钥放在 `/srv/agentswatch/management/credentials`，权限设为 `0600`。容器入口会在首次启动时生成默认的 `agentswatch-managed` Ed25519 密钥；也可以预先放入受控私钥并通过 JSON 映射其他别名。目标服务器的 host key 在“服务器接入与 Ansible 连接”页面扫描，由管理员通过可信渠道核对后随连接记录固定；任务运行时物化为短生命周期 `known_hosts`，不要关闭 Ansible host key 检查。

首次纳管还要求控制端安装 `sshpass`。生产镜像已经包含；直接以 systemd/虚拟环境部署时需要由系统包管理器安装。目标服务器不要求预装 Ansible，平台会用密码首次登录，通过 `raw` 检查或安装 Python 3、写入平台公钥，再以不携带密码的连接复验。服务器密码长期加密保留用于显式重新纳管，普通任务不会自动回退到密码。

### 3. 数据库迁移与启动

先用现有数据库变更流程把 `database/core-assets` 中需要的迁移应用到外部 PostgreSQL。API 启动只同步内置自动化目录，不代替数据库迁移。迁移完成后必须创建首个管理员；密码从标准输入读取，不能放在命令参数或环境文件中：

```bash
read -rsp 'Initial AgentsWatch admin password: ' AGENTSWATCH_INITIAL_PASSWORD; echo
printf '%s\n' "$AGENTSWATCH_INITIAL_PASSWORD" | docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  run --rm --no-deps --entrypoint agentswatch-manage-auth-user management-api \
  --username admin --display-name '系统管理员' --role admin --password-stdin
unset AGENTSWATCH_INITIAL_PASSWORD
```

密码至少 12 位，并且必须同时包含大写字母、小写字母、数字和符号。角色边界如下：

- `admin`：资产、任务、自动化库和用户管理；
- `operator`：资产维护和任务执行；
- `viewer`：只读查看。

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  up -d
```

在仅管理模式下，Compose 启动 `management-api`、`management-mcp` 和 `management-nginx`，只有 Nginx 发布一个 HTTPS 端口；API/MCP 仍只监听 UDS。联合模式使用三文件叠加命令，禁用 `management-nginx` 并启动 `combined-nginx`。MCP 只共享项目私有 run volume，不获得数据库 DSN 或自动化目录。健康检查直接使用 AF_UNIX 请求 run volume 内各自的 socket。可分别验证：

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  exec management-api \
  python -c "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/data/agentswatch/management/run/api.sock'); s.sendall(b'GET /healthz HTTP/1.0\\r\\nHost: localhost\\r\\n\\r\\n'); assert b' 200 ' in s.recv(256).split(b'\\r\\n', 1)[0]"
```

```bash
docker compose \
  --env-file /etc/agentswatch/management.production.env \
  -f deploy/management/compose.production.example.yml \
  exec management-mcp \
  python -c "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/data/agentswatch/management/run/mcp.sock'); s.sendall(b'GET /healthz HTTP/1.0\\r\\nHost: 127.0.0.1:8010\\r\\n\\r\\n'); assert b' 200 ' in s.recv(256).split(b'\\r\\n', 1)[0]"
```

仅管理模式入口为 `https://<管理域名>:<AGENTSWATCH_MANAGEMENT_HTTPS_PORT>/agentswatch/`；联合模式入口为 `https://<统一域名>/agentswatch/`。两种模式都不要使用 host network 或给 API/MCP 单独发布端口。

### 4. 运维适配器对接边界

管理 API 只通过 `AGENTSWATCH_OBSERVABILITY_CONTROL_URL` 调用运维侧固定的预览、确认同步、任务查询和回滚接口，并使用独立 Bearer Token。联合覆盖把 URL 固定为共享 Nginx 未发布的内部 listener；其他远程部署应使用受信 HTTPS 和来源 ACL。不要配置数据库 DSN、Gateway Token、文件路径或 Unix socket。运维适配器自行用专用只读数据库账号查询 `integration.observability_gateway_v2`，管理 API 不代转数据库连接，也不读取运维 revision 目录。

两个变量留空时，“可观测接入”页面显示未配置，其他资产管理、纳管和自动化功能保持可用。运维适配器不可用时，管理 API 只报告连接失败，不会删除或覆盖运维侧最后一个已激活 revision。

### 5. 生产服务器连接约束

容器中的 `127.0.0.1` 只代表 API 容器自身，不能用它连接宿主机。生产 Compose 强制 `AGENTSWATCH_ALLOW_LOCAL_CONNECTION=false`。当前 `.15` 直部署测试使用仅允许本机来源的 SSH 连接；进入容器生产前，需把其 SSH host 从 `127.0.0.1` 调整为容器可达的受控宿主地址，并继续使用工作账号、host key 和凭据文件映射。

## 当前测试环境直部署

直部署只用于本机/测试。源码可发布到 `/srv/agentswatch-management`，Python 虚拟环境位于 `/srv/agentswatch-management/.venv`，数据使用 `/srv/agentswatch/management`。容器镜像内部路径不映射宿主路径。

### 1. 准备账号和目录

当前宿主的数字 GID `101` 对应 `systemd-journal`，所以只把它设为 run 目录的组，绝不把 `agentswatch` 用户加入该组。测试服务仍以目录 owner 权限写入：

```bash
sudo groupadd --system --gid 10001 agentswatch
sudo useradd --system --uid 10001 --gid 10001 --home /srv/agentswatch/management/home --shell /usr/sbin/nologin agentswatch
sudo install -d -o agentswatch -g agentswatch -m 0755 /srv/agentswatch/management
sudo install -d -o agentswatch -g 101 -m 2750 /srv/agentswatch/management/run
sudo install -d -o agentswatch -g agentswatch -m 0700 /srv/agentswatch/management/automation
sudo install -d -o agentswatch -g agentswatch -m 0700 /srv/agentswatch/management/credentials
sudo install -d -o agentswatch -g agentswatch -m 0700 /srv/agentswatch/management/home
sudo install -d -o agentswatch -g agentswatch -m 0755 /srv/agentswatch/management/frontend
```

### 2. 发布并启动

1. 将源码发布到 `/srv/agentswatch-management`，在该目录创建 `.venv` 并安装 `requirements-api.txt` 与 `requirements-mcp.txt`。
2. 启动隔离 PostgreSQL 并等待健康，用迁移脚本执行全部正式迁移，再运行角色配置脚本。迁移器会自动排除仅供本机开发的 `003_local_reader.sql`。
3. 在 `frontend` 执行 `npm ci` 和 `AGENTSWATCH_FRONTEND_BASE=/agentswatch/ npm run build`，把 `dist` 内容复制到 `/srv/agentswatch/management/frontend`。
4. 将 `management.test.env.example` 复制到 `/etc/agentswatch/management.env`，权限设为 `0600`，填入 reader/writer DSN 和外部凭据文件映射。
5. 使用 `/srv/agentswatch-management/scripts/manage_auth_user.py --password-stdin` 创建首个管理员，然后将 `agentswatch-management.service`、`agentswatch-mcp.service` 安装到 `/etc/systemd/system`，执行 daemon-reload 后依次启动 API 与 MCP；确认 `api.sock`、`mcp.sock` 均存在、模式为 `0660`、GID 为 Nginx worker GID。
6. 启动只属于管理模块的测试 Nginx；它只读挂载管理 run 目录并仅在宿主回环地址发布端口。不得改造或重建运维 Nginx：

```bash
docker run -d \
  --name agentswatch-management-test-nginx \
  --restart unless-stopped \
  -p 127.0.0.1:18080:8080 \
  -v /srv/agentswatch/management/run:/data/agentswatch/management/run:ro \
  -v /srv/agentswatch-management/deploy/management/nginx-agentswatch.test.conf.example:/etc/nginx/conf.d/default.conf:ro \
  nginx:stable-alpine
```

systemd 启动前会清理同一路径的陈旧 socket。

测试直部署的宿主 UDS 可直接验证：

```bash
curl --fail \
  --unix-socket /srv/agentswatch/management/run/api.sock \
  http://localhost/healthz

curl --fail \
  --unix-socket /srv/agentswatch/management/run/mcp.sock \
  http://localhost/healthz
```

测试模板也保持 `AGENTSWATCH_ALLOW_LOCAL_CONNECTION=false`。`.15` 通过受限的 loopback SSH 密钥执行任务，从而让 OpenClaw 命令和用户级 systemd 操作处于正确的 `openclaw` 用户上下文；其他清单服务器不创建连接记录。

Ubuntu 22.04 自带 Python 3.10，因此直部署测试会从条件依赖中解析到 Ansible Core 2.17.14。生产镜像使用 Python 3.12，解析到 Ansible Core 2.18–2.19；测试兼容分支不会降低生产镜像的依赖基线。

## 独立测试 PostgreSQL

`compose.test-postgres.yml` 只包含 PostgreSQL 16，使用独立的 Compose bridge network，不加入现有 observability Compose 网络。数据库只绑定宿主 `127.0.0.1`，数据位于 `/srv/agentswatch/management/postgres`。这里不能把该网络标记为 Docker `internal`：部分 Docker 版本会因此令已声明的 loopback published port 不生效；隔离边界由独立网络和仅回环发布共同保证。

将 `.env.test-postgres.example` 复制到源码目录之外，权限设为 `0600` 并填写全部空值，再启动：

```bash
sudo install -d -m 0700 /srv/agentswatch/management/postgres
docker compose \
  --env-file /etc/agentswatch/management.test-postgres.env \
  -f deploy/management/compose.test-postgres.yml \
  up -d
```

该数据库仅用于可复现测试，不是生产数据库，也不复用现有监控数据库账号。

## Runtime WebUI 访问

管理入口不再提供固定 `/openclaw/` 代理，也不把 runtime-web socket 挂进任何 Nginx。运行时详情页按 [运行时 WebUI 访问设计](../../docs/runtime-web-access.md) 建立 SSH 临时隧道、Tailscale Serve 或受控私网直连，并在新标签页打开原生 UI；这条访问链路不参与管理模块与运维模块的数据同步。

## 更新与回滚

- 数据库迁移先独立备份和执行，不能依赖容器启动隐式完成。
- 前端、API 与内置 Playbook 随同一个版本镜像发布，避免资源版本错配。
- 更新时替换镜像 tag 后重建 `management-api`、`management-mcp` 与 `management-nginx` 服务；持久任务产物、受管 Playbook 和凭据不随容器删除。
- 回滚只切换到兼容当前数据库结构的旧镜像。涉及不可逆数据库变更时，按数据库变更方案单独处理。
