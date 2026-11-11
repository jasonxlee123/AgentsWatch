# 运维模块离线镜像包

目标服务器为 `linux/amd64`，已安装 Docker 和 Docker Compose，但不能访问镜像仓库。

本文只说明运维栈的离线镜像包操作；通用部署方式见[统一部署指引](../../docs/deployment-guide.md)。当前脚本不打包管理模块和适配器镜像，联合离线部署还必须单独导出 `AGENTSWATCH_MANAGEMENT_IMAGE` 与 `AGENTSWATCH_OBSERVABILITY_CONTROL_IMAGE`。

## 生成镜像包

在可访问镜像仓库的机器执行：

```bash
PLATFORM=linux/amd64 AGENTSWATCH_IMAGE_TAG=<release-tag> ./scripts/export_offline_images.sh
```

生成：

```text
dist/offline/agentswatch-images-linux-amd64-<release-tag>.tar.gz
dist/offline/agentswatch-images-linux-amd64-<release-tag>.tar.gz.sha256
dist/offline/images-<release-tag>.txt
```

包内镜像保留原始上游名称和 tag，不做二次命名。

需要单独补充 Nginx 镜像时，导出并上传对应的增量包：

```text
dist/offline/nginx-stable-alpine-linux-amd64-<release-tag>.tar.gz
dist/offline/nginx-stable-alpine-linux-amd64-<release-tag>.tar.gz.sha256
```

包内镜像仍为官方原名 `nginx:stable-alpine`。

## 上传并加载

上传项目发布包和镜像包到 `<monitor-host>`，然后执行：

```bash
./scripts/load_offline_images.sh dist/offline/agentswatch-images-linux-amd64-<release-tag>.tar.gz
./scripts/load_offline_images.sh dist/offline/nginx-stable-alpine-linux-amd64-<release-tag>.tar.gz
docker image ls
```

加载脚本会在同名 `.sha256` 文件存在时先校验摘要。

### 已有服务器最小升级

已经加载基础镜像包时，只加载 Nginx 增量包并覆盖项目文件，原 `.env` 和 token 不会进入项目包，也不会被删除。保留原 `.env` 时补齐数据盘和 HTTPS 配置：

```bash
./scripts/load_offline_images.sh \
  dist/offline/nginx-stable-alpine-linux-amd64-<release-tag>.tar.gz

grep -q '^GRAFANA_BIND_ADDRESS=' .env \
  && sed -i 's/^GRAFANA_BIND_ADDRESS=.*/GRAFANA_BIND_ADDRESS=127.0.0.1/' .env \
  || echo 'GRAFANA_BIND_ADDRESS=127.0.0.1' >> .env
grep -q '^GRAFANA_PUBLIC_HOST=' .env || echo 'GRAFANA_PUBLIC_HOST=monitor.example.invalid' >> .env
grep -q '^GRAFANA_PUBLIC_URL=' .env || echo 'GRAFANA_PUBLIC_URL=https://monitor.example.invalid/' >> .env
grep -q '^AGENTSWATCH_DATA_ROOT=' .env || echo 'AGENTSWATCH_DATA_ROOT=/srv/agentswatch' >> .env

docker compose up -d
```

## 配置并启动

```bash
cp .env.example .env
```

修改 `.env` 中的 `GRAFANA_ADMIN_PASSWORD`、`MINIO_ROOT_PASSWORD`、`CLICKHOUSE_PASSWORD`，固定全部生产镜像版本，并配置经管理员核验的 `LOBSTER_SSH_KNOWN_HOSTS`；私钥通过 `LOBSTER_SSH_IDENTITY_FILE` 指定或由当前 SSH agent 提供。工具以 `openclaw` 用户通过只读公钥 SSH 解析 `openclaw.json`，获取各 Gateway 已有 token，不修改远端配置。不要使用会返回脱敏值的 `openclaw config get gateway.auth.token`：

```bash
./scripts/lobsterctl.py token-fetch --dry-run
./scripts/lobsterctl.py token-fetch
./scripts/lobsterctl.py token-status
./scripts/lobsterctl.py render
docker compose config --quiet
docker compose up -d
```

Compose 在启动长期服务前自动运行 `storage-init`、`prometheus-secrets-init` 和 `nginx-tls-init`。它们分别准备 `/srv/agentswatch` 下的持久目录、将 token 修复为 Prometheus 可读的 `0640/65534`，以及保留正式证书或生成自签证书；任何一步失败都会阻止依赖服务启动。正常部署不需要手工 `chown` 或运行证书脚本。

默认网络入口：

- `0.0.0.0:443`：Nginx HTTPS，反向代理 Grafana。
- `0.0.0.0:4318`：OpenClaw OTLP/HTTP。
- Grafana `3000` 和其余管理端口：仅绑定 `127.0.0.1`。

## 验证

```bash
docker compose ps
curl -fsS http://127.0.0.1:13133/
curl -fsS http://127.0.0.1:9090/-/ready
curl -fsS http://127.0.0.1:10902/-/ready
curl -kfsS https://127.0.0.1/nginx-health
./scripts/check_persistence.sh
```

浏览器访问 `https://monitor.example.invalid/`。自签证书需要在客户端导入信任；正式环境可用组织证书替换 `nginx/certs/tls.crt` 和 `tls.key`。MinIO Console 等管理界面建议通过 SSH 隧道访问。

升级镜像时重新生成并上传一个完整 bundle；`docker load` 后执行 `docker compose up -d` 即可按相同上游名称切换镜像。
