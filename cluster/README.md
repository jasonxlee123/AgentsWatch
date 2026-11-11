# 运维独立模式本地 JSON 清单

`cluster/lobsters.json` 是**仅运维模式**和旧 CLI 工作流的兼容清单，不是所有部署方式的唯一事实来源。联合模式以管理数据库 `integration.observability_gateway_v2` 为资产事实源，由运维适配器生成并激活 `/data/agentswatch/observability-control/current` revision；不要在同一环境同时人工维护两套权威清单。

推荐的仅运维模式也使用 `observabilityctl.py --source local-json` 生成动态 revision。下列文件属于旧 `lobsterctl.py render` 兼容工作流，不能再视为主 Compose 的默认动态配置：

- `prometheus/scrape_configs/openclaw-gateways.yml`：每只 Gateway 一个原生 Prometheus scrape job，各自引用独立凭据文件。
- `cluster/openclaw-monitoring-patches/<gateway_id>.json`：对应 Gateway 的完整诊断配置补丁。

三种部署模式和当前动态配置路径见[部署指引](../docs/deployment-guide.md)，适配器契约见[运维清单控制适配器](../agentswatch_observability/README.md)。

## 清单字段

公共默认值放在 `defaults`，单只龙虾只写差异项。

| 字段 | 用途 |
| --- | --- |
| `gateway_id` | 稳定唯一 ID，也是 Grafana 下钻维度；仅允许字母、数字、点、下划线和短横线 |
| `host` / `port` | Prometheus 抓取地址 |
| `gateway_bind` | 可选的 OpenClaw Gateway 监听模式；省略时不改现有监听配置 |
| `native_scrape_expected` | 可选布尔值；是否要求原生 Prometheus 直抓健康，默认由 `gateway_bind` 自动推导 |
| `enabled` | 是否纳入当前采集；停用后历史指标仍保留 |
| `deployment_mode` | `baremetal` 或 `container` |
| `container_name` | 容器部署时的 OpenClaw 容器名 |
| `ssh_user` / `ssh_port` | 单只龙虾覆盖 `.env` 中的 SSH 默认值 |
| `openclaw_user` | 裸金属部署时需要切换到的 OpenClaw Unix 用户；直接以该用户 SSH 时留空 |
| `openclaw_config_path` | 单只龙虾覆盖 OpenClaw 配置文件路径 |
| `container_user` | 容器部署时可选的容器内用户 |
| `cluster` / `env` / `region` / `team` | 低基数聚合维度 |
| `otel_endpoint` | OTLP/HTTP Collector 地址 |
| `sample_rate` | trace 根采样率，不影响 metrics |
| `flush_interval_ms` | OTel 指标和批处理刷新间隔 |
| `stuck_session_warn_ms` | 会话无进展后的诊断分类阈值，默认 120 秒 |
| `stuck_session_abort_ms` | 停滞工作进入恢复中止的阈值，默认 360 秒 |
| `lobster_name` / `department` / `service_mode` | 看板展示和运营筛选字段 |
| `plugins_allow` | 仅在需要声明目标机完整插件 allow list 时填写 |

示例见 [lobsters.example.json](lobsters.example.json)。

## 生成配置

```bash
./scripts/lobsterctl.py render
```

生成器会：

1. 校验清单中的必填字段和 `gateway_id`。
2. 只为 `enabled=true` 的 Gateway 生成独立抓取任务和补丁。
3. 清理已经停用或删除 Gateway 的旧补丁。
4. 将 OTLP 端点、协议、三类信号、全量 trace、刷新、会话存活阈值、隐私开关和身份 headers 全部写入 OpenClaw 配置；仅在清单显式声明时写入 `gateway.bind`。

可临时覆盖所有龙虾的 Collector 地址：

```bash
./scripts/lobsterctl.py render --otel-endpoint http://collector.example.invalid:4318
```

## 增删改

```bash
# 查看
./scripts/lobsterctl.py list

# 新增裸金属
./scripts/lobsterctl.py add \
  --gateway-id gateway-example-001 \
  --host gateway-001.example.invalid \
  --deployment-mode baremetal \
  --lobster-name 示例智能体 \
  --department 示例业务部

./scripts/lobsterctl.py token-set gateway-example-001
./scripts/lobsterctl.py render --reload

# 新增容器
./scripts/lobsterctl.py add \
  --gateway-id gateway-example-002 \
  --host gateway-002.example.invalid \
  --deployment-mode container \
  --container-name openclaw

./scripts/lobsterctl.py token-set gateway-example-002
./scripts/lobsterctl.py render --reload

# 停用、恢复、硬删除
./scripts/lobsterctl.py disable gateway-example-001 --render --reload
./scripts/lobsterctl.py enable gateway-example-001 --render --reload
./scripts/lobsterctl.py delete gateway-example-001 --render --reload
```

优先使用 `disable`，以便保留清单信息和历史数据的含义。

## Gateway 准备

两个插件需要在每只龙虾上安装一次：

```bash
openclaw plugins install clawhub:@openclaw/diagnostics-otel
openclaw plugins install clawhub:@openclaw/diagnostics-prometheus
```

下发脚本不会在线安装插件。它先检查插件存在并执行 `openclaw config patch --dry-run`，通过后再调用 `openclaw plugins enable`，因此已有严格 `plugins.allow` 时也会保留其他插件并加入两个诊断插件。

OTel 推送不依赖 Gateway 的监听地址，`gateway.bind="loopback"` 可以正常回流 metrics、traces 和 logs。只有中央 Prometheus 需要直接抓取 Gateway 路由时，才为对应实例设置 `gateway_bind: "lan"`；补丁仍不会覆盖 `gateway.auth.token`、认证模式或端口，主机防火墙应只允许已批准的监控服务器访问清单端口。若组织使用 Tailnet，可设置为 `tailnet` 并同步调整抓取地址。

裸金属远程下发不依赖非交互 SSH 的默认 `PATH`。脚本会以清单指定的 SSH 用户通过受控的交互 Bash 解析该用户 `~/.bashrc` 中的 PATH，并兼容 NVM、pnpm、Volta、mise、asdf、Bun 等常见安装位置，同时补齐 Node 路径；不会重新安装 OpenClaw 或修改用户 shell 配置。

```bash
# 裸金属
./scripts/apply_lobster_config.sh gateway-example-001 --ssh openclaw@gateway-001.example.invalid --dry-run
./scripts/apply_lobster_config.sh gateway-example-001 --ssh openclaw@gateway-001.example.invalid

# 容器
./scripts/apply_lobster_config.sh gateway-example-002 --ssh openclaw@gateway-002.example.invalid --container openclaw --dry-run
./scripts/apply_lobster_config.sh gateway-example-002 --ssh openclaw@gateway-002.example.invalid --container openclaw
```

批量操作时先对全部目标执行 `--dry-run`，确认版本和插件一致后再分批正式下发。

## Prometheus 认证

`/api/diagnostics/prometheus` 是受保护的 operator 路由，复用每只 Gateway 已有的 `gateway.auth.token` 或 `OPENCLAW_GATEWAY_TOKEN`。现有 token 不做任何修改，只在监控服务器保存一个权限为 `0640`、组为 Prometheus GID 的独立副本：

```bash
sudo ./scripts/prepare_prometheus_secrets.sh
./scripts/lobsterctl.py token-set lobster-034
./scripts/lobsterctl.py token-status
```

OpenClaw 配置路径由清单或 `LOBSTER_OPENCLAW_CONFIG_PATH` 指定。服务器首次纳管可以使用管理员录入的账号密码安装并复验平台公钥；复验通过后，`./scripts/lobsterctl.py token-fetch` 与配置下发一样只允许公钥 SSH，不会在公钥失败时回退密码。`token-fetch` 必须使用 `--known-hosts`（或 `.env` 的 `LOBSTER_SSH_KNOWN_HOSTS`）指定管理员核验并钉住的非空主机密钥文件；可用 `--identity-file`/`LOBSTER_SSH_IDENTITY_FILE` 指定平台私钥，留空时使用当前 SSH agent。连接固定启用 `StrictHostKeyChecking=yes`、`BatchMode=yes` 并禁用密码和键盘交互认证。远端命令通过 Node 只读解析已配置的 OpenClaw JSON/JSON5 文件，不会修改配置。不能使用 `openclaw config get gateway.auth.token`，该命令会返回脱敏占位值，无法通过 Gateway 认证。`token-collect` 和 `token-set` 仅作为个别 SSH 失败时的隐藏输入兜底。机密不会进入命令行、Git 清单或 Prometheus labels。生成器利用 Prometheus 原生 `scrape_config_files` 为每只龙虾生成独立 job，并引用 `/etc/prometheus/secrets/gateways/<gateway_id>.token`。目录使用 setgid `2750`，后续 token 会继承 Prometheus GID 并以 `0640` 写入。新增、停用或删除后执行 `render --reload`；只要任一启用龙虾缺少安全的 token 文件，热加载就会被拒绝。

## 验收

配置下发并产生一笔真实模型调用后：

```bash
./scripts/check_openclaw_telemetry.py \
  --prometheus-url http://127.0.0.1:9090 \
  --gateway-id lobster-034 \
  --skip-native
```

OTel bridge core 指标应立即通过；usage 指标需要模型调用链路实际报告 token/cost 才会出现。启用原生直抓后移除 `--skip-native` 并提供 `--gateway-url`；此时 target 必须为 `up=1`，仅看到带目标标签的 `up=0` 不代表原生指标已经抓取成功。
