# 运营数据持久化设计

## 数据口径

OpenClaw 的诊断事件是进程内结构化事件。官方 `diagnostics-otel` 插件订阅事件后，将其投影为 OTLP metrics、traces 和 logs；诊断事件不是 OTLP 的第四类信号，也没有官方原始事件流水端点。

本项目只保存 OpenClaw 官方插件实际导出的数据：

| 数据 | 实时查询与权威口径 | 分析副本 | 默认保留 |
| --- | --- | --- | --- |
| OTel metrics | Collector Prometheus exporter -> Prometheus 15 天 -> Thanos 长期查询，是运营统计主口径 | ClickHouse 全属性明细 | Thanos raw 90 天、5m 2 年、1h 5 年；ClickHouse 365 天 |
| 原生 Prometheus metrics | 可选的 Gateway 直抓独立校验面 | 不与 OTel 指标相加 | 随 Prometheus/Thanos 策略 |
| OTel traces/spans | Tempo | ClickHouse `otel_traces` | Tempo 30 天，ClickHouse 365 天 |
| OTel logs | Loki | ClickHouse `otel_logs` | Loki 90 天，ClickHouse 365 天 |

## 数据盘布局

部署时将 `AGENTSWATCH_DATA_ROOT` 设置为持久数据盘，例如 `/srv/agentswatch`；主 Compose 将所有状态直接 bind mount 到该目录，不使用 Docker 命名卷：

| 子目录 | 服务 | 容器内目录 | 初始化属主 |
| --- | --- | --- | --- |
| `minio` | MinIO | `/data` | `0:0` |
| `clickhouse` | ClickHouse | `/var/lib/clickhouse` | `101:101` |
| `prometheus` | Prometheus / Thanos Sidecar | `/prometheus` | `65534:65534` |
| `alertmanager` | Alertmanager | `/alertmanager` | `65534:65534` |
| `otel-collector` | Collector 持久队列 | `/var/lib/otelcol` | `10001:10001` |
| `loki` | Loki 本地状态 | `/loki` | `10001:10001` |
| `tempo` | Tempo 本地状态 | `/var/tempo` | `10001:10001` |
| `grafana` | Grafana | `/var/lib/grafana` | `472:0` |
| `thanos-store` | Thanos Store | `/var/thanos/store` | `0:0` |
| `thanos-compact` | Thanos Compactor | `/var/thanos/compact` | `0:0` |

`storage-init` 只校正这些顶层目录，不递归遍历历史数据。配置文件、Gateway token 和 HTTPS 证书应位于受限的配置目录，例如 `/srv/agentswatch/config`；它们属于配置备份，不属于运行数据目录。

token、cost 等运营汇总以 `diagnostics-otel` 导出并由 Collector Prometheus exporter 桥接的指标为准。`diagnostics-prometheus` 原生端点保留为独立拉取与交叉校验面，不与 OTel 指标相加；ClickHouse 中的 OTel metrics 是分析副本，也不作为计费账本。Collector Contrib 当前将 ClickHouse traces/logs exporter 标记为 beta、metrics exporter 标记为 alpha，因此指标长期口径不能反过来依赖 ClickHouse。

`openclaw.tokens`、`openclaw.cost.usd` 等 OTel Sum 是累计量快照。ClickHouse 会按刷新周期保存多个 `Value`，禁止直接 `sum(Value)`；每日增量使用 Prometheus/Thanos 的 `increase()`，或在 ClickHouse 中按相同属性分组后计算时间窗口首尾差值。`openclaw.model.usage` span 是逐次运行记录，可用于模型调用明细核对，但不能与累计 metric 再次相加。

## 全量信号设置

- `traces`、`metrics`、`logs` 全部开启，协议固定为 `http/protobuf`。
- `sampleRate=1.0`，保留所有根 trace 及其子 span。
- Collector 的 ClickHouse 归档 pipeline 不删除 `session_id` 等高基数属性；Prometheus pipeline 仍做基数保护。
- Tempo、Loki 和 ClickHouse exporter 使用 `file_storage` 持久队列及无限时重试，Collector 重启后继续发送。
- `captureContent.*` 全部关闭。运营元数据、token、cost、模型、会话和工具状态会导出，提示词、响应、工具载荷、系统提示和日志正文不会导出。

## 官方导出覆盖

当前配置接收官方文档列出的全部 metrics 类别：模型用量与成本、模型调用、故障转移、技能、消息、Talk、队列、会话与恢复、存活性、harness、工具、exec、内存、载荷和 exporter 健康。

全部官方 span 形态进入 Tempo 和 ClickHouse，包括 `openclaw.model.usage`、`openclaw.run`、`openclaw.model.call`、`openclaw.harness.run`、`openclaw.tool.execution`、`openclaw.exec`、webhook、消息、`openclaw.session.stuck`、上下文组装、工具循环和内存压力。

诊断事件目录中的事件通过这些官方投影进入存储。需要特别区分：`session.long_running`、`session.stalled`、`run.progress` 等事件在当前官方 `diagnostics-otel` 中不形成独立 metric、span 或 log，因此无法从 OTLP 还原为逐条原始事件。若未来必须保存每一条原始事件对象，需要 OpenClaw 提供稳定的可信事件 exporter，或另行评审一个自定义 Gateway 插件；当前方案不会调用内部不稳定 API 冒充“全量”。

## 面向管理服务

ClickHouse 使用 Collector Contrib 自带 exporter 自动创建以下表：

- `otel_traces`
- `otel_logs`
- `otel_metrics_gauge`
- `otel_metrics_sum`
- `otel_metrics_summary`
- `otel_metrics_histogram`
- `otel_metrics_exponential_histogram`

所有表保留 `ResourceAttributes`，可按 `cluster`、`gateway_id`、环境、地域、团队和部署方式过滤。未来驾驶舱或管理 API 应通过独立只读 ClickHouse 用户访问这些表，不直接依赖 Grafana 查询。

示例：按龙虾统计最近一天的 span：

```sql
SELECT
  ResourceAttributes['gateway_id'] AS gateway_id,
  SpanName,
  count() AS spans
FROM otel.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 DAY
GROUP BY gateway_id, SpanName
ORDER BY spans DESC;
```

## 容量与可用性评估

目前是单机部署：MinIO、ClickHouse、Prometheus、Tempo、Loki 都存在单机故障域，适合第一阶段 30 至 50 多只 Gateway 的集中纳管和数据模型验证，但不等同于高可用平台。

上线后至少运行 7 天，再按实际写入量规划磁盘：

```sql
SELECT
  database,
  table,
  formatReadableSize(sum(bytes_on_disk)) AS disk,
  sum(rows) AS rows
FROM system.parts
WHERE active AND database = 'otel'
GROUP BY database, table
ORDER BY sum(bytes_on_disk) DESC;
```

规划公式为 `日均压缩写入量 x 保留天数 x 1.3`。磁盘利用率达到 60% 时复核增长率，70% 告警，80% 前完成扩容或调整 TTL。Collector 的持久队列覆盖后端短时故障和进程重启，但不是无限容量的永久缓存；仍需监控 exporter 失败、队列长度和 `openclaw.diagnostic.async_queue.dropped`。生产高可用阶段应将 MinIO 改为多盘/分布式对象存储，并将 ClickHouse 改为复制表加 Keeper；上层数据契约和 Collector 接入方式无需变化。

`CLICKHOUSE_TTL` 只在 exporter 首次创建表时写入 DDL。已经创建表后修改环境变量不会自动改旧表 TTL，需要执行相应 `ALTER TABLE ... MODIFY TTL` 迁移。
