# Grafana 看板与运营报表

## 看板体系

| 看板 | 用途 | 核心内容 |
| --- | --- | --- |
| 龙虾集群总览 | 集群态势和异常发现 | OTel 活跃、原生直抓状态、Token 与成本、模型和 Provider、会话与队列、错误与时延、资源和遥测健康 |
| 龙虾实例详情 | 按 `gateway_id` 下钻 | 单只龙虾的 Token、模型、Provider、Run、Harness、工具、技能、会话、队列、资源和诊断状态 |
| 龙虾运营报表 | 周期汇总与上报 | 龙虾、模型、Provider、错误、会话、工具、Harness 和稳定性明细表 |

运营统计使用 `OpenClaw -> OTLP -> Collector prometheus exporter -> Prometheus` 路径的指标，当前序列 `job="openclaw-gateway"`。原生 `diagnostics-prometheus` 拉取的 `job="openclaw-gateways"` 只用于独立抓取健康和实例清单元数据，不与 OTel 运营指标相加。OTel traces 和 logs 继续用于链路与事件诊断。

## 时间与筛选

- 总览和实例详情默认显示最近 24 小时，运营报表默认显示本月。
- 运营报表提供今日、本周、本月、上月、本季度和本年快捷入口。
- Grafana 时间选择器支持任意相对或绝对起止时间，因此可以查看指定日、周、月、季度、年度或自定义区间。
- 集群、龙虾、部门、地域、团队、Provider 和模型变量按看板用途提供；跨看板导航保留当前变量和时间。
- 累计值使用当前选定区间内的 counter 增量；趋势图使用该区间内的速率或每日增量。

## 运营口径

| 指标 | 口径 |
| --- | --- |
| Token 总量 | `openclaw_tokens_total{openclaw_token="total"}` |
| 输入/输出 Token | `openclaw_token="input"` / `openclaw_token="output"` |
| 缓存 Token | `cache_read + cache_write` |
| 推理 Token | `openclaw_token="reasoning"`；Provider 未上报时为 0 |
| Token 构成 | 排除 `total` 和 `prompt`，避免包含关系导致重复相加 |
| 模型调用 | `openclaw_model_call_duration_ms_milliseconds_count`，按 `openclaw_provider` 和 `openclaw_model` 聚合 |
| 模型错误 | `openclaw_error_category` 非空的模型调用 |
| 成本 | `openclaw_cost_usd_total`，单位 USD；未收到官方 cost 时为 0，不做价格推算 |
| 会话 | `openclaw_session_turn_created_total`，表示会话轮次，不表示去重用户或去重 session |
| OTel 活跃 | Collector 暴露的 `target_info{job="openclaw-gateway"}` |
| 原生直抓 | `up{job="openclaw-gateways"}`，与 OTel 数据活跃分开显示 |

复合报表会为没有推理 Token、错误或成本的数据补 0，保证 CSV 的列结构稳定。未产生任何调用和 Token 的模型不会虚构一行记录。

## 月报操作

1. 打开 `龙虾运营报表`，选择“上月”；需要自然月之外的口径时设置绝对起止时间。
2. 确认集群、龙虾、部门、Provider 和模型筛选条件；全量月报保持为 `All`。
3. 导出 `龙虾运营汇总`、`模型运营汇总` 和 `Provider 运营汇总` 的 CSV。
4. 按需附加模型错误、会话状态、工具/Harness 和稳定性附表。
5. 在导出文件名或上报材料中记录时间区间和筛选条件，确保报表可复核。

Grafana OSS 的 CSV 导出位于面板菜单的 Inspect/Data（不同版本可能显示为 Export）。当前方案提供可复核的数据报表，不依赖截图或手工抄数。

## 维护与校验

看板 JSON 由生成器维护，不直接编辑生成产物：

```bash
./scripts/generate_grafana_dashboards.py
./scripts/check_grafana_queries.py
```

查询校验脚本会替换 Grafana 变量并向本机 Prometheus 验证每条 PromQL。看板使用 Grafana 内置面板，不需要额外插件，也不会增加离线部署镜像。
