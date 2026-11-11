#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "grafana" / "dashboards"

DS = {"type": "prometheus", "uid": "${datasource}"}


def prom(expr, ref_id="A", legend="", instant=False, fmt="time_series", interval=None):
    target = {
        "datasource": DS,
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "refId": ref_id,
    }
    if instant:
        target["instant"] = True
    if fmt:
        target["format"] = fmt
    if interval:
        target["interval"] = interval
    return target


def grid(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def threshold_config(steps=None):
    return {
        "mode": "absolute",
        "steps": steps
        or [
            {"color": "green", "value": None},
            {"color": "orange", "value": 70},
            {"color": "red", "value": 90},
        ],
    }


def field_config(
    unit=None,
    decimals=None,
    color=None,
    links=None,
    mappings=None,
    custom=None,
    min_value=None,
    max_value=None,
    steps=None,
    overrides=None,
):
    defaults = {
        "mappings": mappings or [],
        "thresholds": threshold_config(steps),
    }
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    if color:
        defaults["color"] = color
    if links:
        defaults["links"] = links
    if custom:
        defaults["custom"] = custom
    if min_value is not None:
        defaults["min"] = min_value
    if max_value is not None:
        defaults["max"] = max_value
    return {"defaults": defaults, "overrides": overrides or []}


def row(panel_id, title, y):
    return {
        "id": panel_id,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": grid(0, y, 24, 1),
        "panels": [],
    }


def stat(panel_id, title, expr, pos, unit=None, decimals=None, color="blue", description=""):
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": pos,
        "fieldConfig": field_config(
            unit=unit,
            decimals=decimals,
            color={"mode": "fixed", "fixedColor": color},
        ),
        "options": {
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "auto",
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "textMode": "auto",
            "wideLayout": True,
        },
        "targets": [prom(expr, instant=True)],
    }


def timeseries(
    panel_id,
    title,
    targets,
    pos,
    unit=None,
    decimals=None,
    description="",
    stacking="none",
    legend="bottom",
):
    if isinstance(targets, str):
        targets = [prom(targets)]
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": pos,
        "fieldConfig": field_config(
            unit=unit,
            decimals=decimals,
            color={"mode": "palette-classic"},
            custom={
                "axisCenteredZero": False,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 18,
                "gradientMode": "opacity",
                "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                "lineInterpolation": "smooth",
                "lineWidth": 2,
                "pointSize": 4,
                "scaleDistribution": {"type": "linear"},
                "showPoints": "never",
                "spanNulls": False,
                "stacking": {"group": "A", "mode": stacking},
                "thresholdsStyle": {"mode": "off"},
            },
        ),
        "options": {
            "legend": {
                "calcs": ["lastNotNull", "max"],
                "displayMode": "table" if legend == "bottom" else "list",
                "placement": legend,
                "showLegend": True,
            },
            "tooltip": {"hideZeros": True, "mode": "multi", "sort": "desc"},
        },
        "targets": targets,
    }


def gateway_data_link(value="${__field.labels.gateway_id}", cluster="${__field.labels.cluster}"):
    return {
        "title": "查看龙虾详情",
        "url": (
            "/d/lobster-gateway-detail/lobster-gateway-detail"
            f"?var-datasource=${{datasource}}&var-cluster={cluster}&var-gateway_id={value}"
            "&from=${__from}&to=${__to}"
        ),
    }


def bargauge(
    panel_id,
    title,
    expr,
    pos,
    unit=None,
    decimals=None,
    description="",
    legend="{{gateway_id}}",
    color="cyan",
    gateway_links=False,
):
    links = [gateway_data_link()] if gateway_links else None
    return {
        "id": panel_id,
        "type": "bargauge",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": pos,
        "fieldConfig": field_config(
            unit=unit,
            decimals=decimals,
            links=links,
            color={"mode": "continuous-GrYlRd" if color == "heat" else "fixed", "fixedColor": color},
        ),
        "options": {
            "displayMode": "gradient",
            "maxVizHeight": 300,
            "minVizHeight": 18,
            "minVizWidth": 0,
            "namePlacement": "auto",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showUnfilled": True,
            "sizing": "auto",
            "valueMode": "color",
        },
        "targets": [prom(expr, legend=legend, instant=True)],
    }


def piechart(panel_id, title, expr, pos, legend, unit=None, decimals=None, description=""):
    return {
        "id": panel_id,
        "type": "piechart",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": pos,
        "fieldConfig": field_config(
            unit=unit,
            decimals=decimals,
            color={"mode": "palette-classic"},
            custom={"hideFrom": {"legend": False, "tooltip": False, "viz": False}},
        ),
        "options": {
            "displayLabels": ["name", "percent"],
            "legend": {"displayMode": "table", "placement": "right", "showLegend": True, "values": ["value", "percent"]},
            "pieType": "donut",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "tooltip": {"hideZeros": True, "mode": "single", "sort": "desc"},
        },
        "targets": [prom(expr, legend=legend, instant=True)],
    }


def table(panel_id, title, targets, pos, description="", transformations=None, overrides=None):
    if isinstance(targets, str):
        targets = [prom(targets, instant=True, fmt="table")]
    return {
        "id": panel_id,
        "type": "table",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": pos,
        "fieldConfig": field_config(
            custom={
                "align": "auto",
                "cellOptions": {"type": "auto"},
                "filterable": True,
                "inspect": False,
            },
            overrides=overrides,
        ),
        "options": {
            "cellHeight": "sm",
            "footer": {"countRows": False, "fields": "", "reducer": ["sum"], "show": False},
            "showHeader": True,
            "sortBy": [],
        },
        "targets": targets,
        "transformations": transformations or [],
    }


def state_timeline(panel_id, title, expr, pos, description=""):
    mappings = [
        {
            "type": "value",
            "options": {
                "0": {"color": "red", "index": 0, "text": "离线"},
                "1": {"color": "green", "index": 1, "text": "在线"},
            },
        }
    ]
    return {
        "id": panel_id,
        "type": "state-timeline",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": pos,
        "fieldConfig": field_config(
            mappings=mappings,
            color={"mode": "thresholds"},
            min_value=0,
            max_value=1,
            steps=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
            custom={
                "fillOpacity": 85,
                "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                "lineWidth": 0,
                "spanNulls": False,
            },
        ),
        "options": {
            "alignValue": "left",
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": False},
            "mergeValues": True,
            "rowHeight": 0.8,
            "showValue": "never",
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [prom(expr, legend="{{lobster_name}}-{{server_display_id}}")],
    }


def datasource_var():
    return {
        "name": "datasource",
        "label": "数据源",
        "type": "datasource",
        "query": "prometheus",
        "regex": "",
        "current": {"selected": True, "text": "Thanos", "value": "thanos"},
        "hide": 0,
    }


def query_var(name, label, query, include_all=True, multi=False, current_all=True):
    current = {"selected": current_all, "text": "全部", "value": "$__all"} if include_all else {}
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "definition": query,
        "query": {"query": query, "refId": "StandardVariableQuery"},
        "refresh": 1,
        "regex": "",
        "sort": 1,
        "includeAll": include_all,
        "allValue": ".*" if include_all else None,
        "multi": multi,
        "current": current,
        "hide": 0,
    }


def nav_links():
    return [
        {
            "type": "link",
            "title": "集群总览",
            "url": "/d/lobster-overview/lobster-overview",
            "icon": "dashboard",
            "includeVars": True,
            "keepTime": True,
            "targetBlank": False,
        },
        {
            "type": "link",
            "title": "龙虾详情",
            "url": "/d/lobster-gateway-detail/lobster-gateway-detail",
            "icon": "drilldown",
            "includeVars": True,
            "keepTime": True,
            "targetBlank": False,
        },
        {
            "type": "link",
            "title": "运营报表",
            "url": "/d/lobster-operations-report/lobster-operations-report",
            "icon": "file-alt",
            "includeVars": True,
            "keepTime": True,
            "targetBlank": False,
        },
    ]


def report_period_links():
    base = "/d/lobster-operations-report/lobster-operations-report"
    periods = [
        ("今日", "now/d", "now"),
        ("本周", "now/w", "now"),
        ("本月", "now/M", "now"),
        ("上月", "now-1M/M", "now/M"),
        ("本季度", "now/Q", "now"),
        ("本年", "now/y", "now"),
    ]
    return [
        {
            "type": "link",
            "title": title,
            "url": f"{base}?from={start}&to={end}",
            "icon": "calendar-alt",
            "includeVars": True,
            "keepTime": False,
            "targetBlank": False,
        }
        for title, start, end in periods
    ]


def base_dashboard(uid, title, description, tags, panels, variables, links=None, time_from="now-24h"):
    return {
        "annotations": {
            "list": [
                {
                    "builtIn": 1,
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "hide": True,
                    "iconColor": "rgba(0, 211, 255, 1)",
                    "name": "告警与注释",
                    "type": "dashboard",
                }
            ]
        },
        "description": description,
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": links or nav_links(),
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 39,
        "tags": tags,
        "templating": {"list": variables},
        "time": {"from": time_from, "to": "now"},
        "timepicker": {
            "hidden": False,
            "refresh_intervals": ["15s", "30s", "1m", "5m", "15m", "30m", "1h"],
            "time_options": ["1d", "7d", "30d", "90d", "1y"],
        },
        "timezone": "browser",
        "title": title,
        "uid": uid,
        "version": 1,
        "weekStart": "monday",
    }


def summary_transform(join_field, rename, order, exclude=None):
    excluded = {"Time": True, "__name__": True}
    excluded.update(exclude or {})
    return [
        {"id": "joinByField", "options": {"byField": join_field, "mode": "outer"}},
        {
            "id": "organize",
            "options": {
                "excludeByName": excluded,
                "includeByName": {},
                "indexByName": order,
                "renameByName": rename,
            },
        },
    ]


def value_override(name, unit=None, decimals=None, links=None, color=None):
    properties = []
    if unit:
        properties.append({"id": "unit", "value": unit})
    if decimals is not None:
        properties.append({"id": "decimals", "value": decimals})
    if links:
        properties.append({"id": "links", "value": links})
    if color:
        properties.append({"id": "color", "value": {"mode": "fixed", "fixedColor": color}})
    return {"matcher": {"id": "byName", "options": name}, "properties": properties}


def overview_dashboard():
    native = 'job="openclaw-gateways",cluster=~"$cluster",department=~"$department",region=~"$region",team=~"$team"'
    inventory = 'inventory_state="active",cluster=~"$cluster",department=~"$department",region=~"$region",team=~"$team"'
    otel = 'job="openclaw-gateway"'
    identity = f'max by (gateway_id) (agentswatch_gateway_info{{{inventory}}})'
    identity_info = f'max by (cluster, gateway_id, lobster_name, server_display_id, department) (agentswatch_gateway_info{{{inventory}}})'

    def selected(expr):
        return f'({expr}) and on (gateway_id) ({identity})'

    def increase(metric, matchers="", period="$__range"):
        return selected(f'increase({metric}{{{otel}{matchers}}}[{period}])')

    def rate(metric, matchers=""):
        return selected(f'rate({metric}{{{otel}{matchers}}}[$__rate_interval])')

    def enrich(expr, labels="cluster, lobster_name, server_display_id"):
        return (
            f'({expr}) * on (gateway_id) group_left({labels}) '
            f'(0 * ({identity_info}) + 1)'
        )

    tokens_total = increase("openclaw_tokens_total", ',openclaw_token="total"')
    model_calls = increase("openclaw_model_call_duration_ms_milliseconds_count")
    model_errors = increase(
        "openclaw_model_call_duration_ms_milliseconds_count",
        ',openclaw_error_category=~".+"',
    )
    session_turns = increase("openclaw_session_turn_created_total")
    costs = increase("openclaw_cost_usd_total")
    otel_active = selected(f'max by (gateway_id) (target_info{{{otel}}})')
    token_components = increase(
        "openclaw_tokens_total",
        ',openclaw_token=~"input|output|cache_read|cache_write|reasoning"',
    )
    token_component_rate = rate(
        "openclaw_tokens_total",
        ',openclaw_token=~"input|output|cache_read|cache_write|reasoning"',
    )
    model_call_rate = rate("openclaw_model_call_duration_ms_milliseconds_count")
    model_error_rate = rate(
        "openclaw_model_call_duration_ms_milliseconds_count",
        ',openclaw_error_category=~".+"',
    )
    rss_average = (
        f'sum by (gateway_id) ({rate("openclaw_memory_rss_bytes_sum")}) '
        f'/ clamp_min(sum by (gateway_id) ({rate("openclaw_memory_rss_bytes_count")}), 0.000001)'
    )
    panels = [
        row(1, "核心态势 · 当前选定区间", 0),
        stat(2, "OTel 活跃龙虾", f'count({otel_active}) or vector(0)', grid(0, 1, 6, 4), decimals=0, color="green", description="Collector 当前仍在暴露该 Gateway 的 OTel resource。"),
        stat(3, "龙虾总数", f'count(max by (gateway_id) (agentswatch_gateway_info{{{inventory}}})) or vector(0)', grid(6, 1, 6, 4), decimals=0, color="blue"),
        stat(4, "Prometheus 直抓在线", f'sum(max by (cluster, gateway_id) (up{{{native}}})) or vector(0)', grid(12, 1, 6, 4), decimals=0, color="cyan", description="原生 diagnostics-prometheus 抓取状态；不影响 OTel 已回流的运营数据。"),
        stat(5, "Token 总量", f'sum({tokens_total}) or vector(0)', grid(18, 1, 6, 4), unit="short", decimals=0, color="purple"),
        stat(6, "模型调用", f'sum({model_calls}) or vector(0)', grid(0, 5, 6, 4), unit="short", decimals=0, color="blue"),
        stat(7, "会话轮次", f'sum({session_turns}) or vector(0)', grid(6, 5, 6, 4), unit="short", decimals=0, color="cyan", description="OpenClaw 不导出原始 session ID，因此以 session.turn.created 作为可聚合的会话工作量口径。"),
        stat(8, "模型成本", f'sum({costs}) or vector(0)', grid(12, 5, 6, 4), unit="currencyUSD", decimals=4, color="orange", description="仅展示 OpenClaw 实际导出的 cost，不使用本系统自建价格账本。"),
        stat(9, "模型错误率", f'(sum({model_errors}) / clamp_min(sum({model_calls}), 1)) or vector(0)', grid(18, 5, 6, 4), unit="percentunit", decimals=2, color="red"),

        row(10, "Token 消耗 · 构成、趋势与排名", 9),
        piechart(11, "Token 构成", f'sum by (openclaw_token) ({token_components})', grid(0, 10, 8, 8), "{{openclaw_token}}", unit="short", decimals=0, description="构成图排除 total 和派生的 prompt，避免包含关系重复相加。"),
        timeseries(12, "Token 消耗速率", [prom(f'sum by (openclaw_token) ({token_component_rate})', legend="{{openclaw_token}}")], grid(8, 10, 16, 8), unit="tps", decimals=2, stacking="normal"),
        bargauge(13, "龙虾 Token 排名", f'topk(15, {enrich(f"sum by (gateway_id) ({tokens_total})")})', grid(0, 18, 8, 8), unit="short", decimals=0, legend="{{lobster_name}}-{{server_display_id}}", gateway_links=True),
        bargauge(14, "模型 Token 排名", f'topk(15, sum by (openclaw_provider, openclaw_model) ({tokens_total}))', grid(8, 18, 8, 8), unit="short", decimals=0, legend="{{openclaw_provider}} / {{openclaw_model}}", color="purple"),
        bargauge(15, "Provider Token 排名", f'topk(15, sum by (openclaw_provider) ({tokens_total}))', grid(16, 18, 8, 8), unit="short", decimals=0, legend="{{openclaw_provider}}", color="orange"),

        row(16, "模型与 Provider · 调用、成本、时延与错误", 26),
        timeseries(17, "模型调用趋势", [
            prom(f'sum({model_call_rate}) * 60', ref_id="A", legend="全部调用"),
            prom(f'sum({model_error_rate}) * 60', ref_id="B", legend="错误调用"),
        ], grid(0, 27, 12, 8), unit="suffix: 次/分", decimals=2),
        bargauge(18, "模型调用次数", f'topk(20, sum by (openclaw_provider, openclaw_model) ({model_calls}))', grid(12, 27, 12, 8), unit="short", decimals=0, legend="{{openclaw_provider}} / {{openclaw_model}}", color="blue"),
        piechart(19, "Provider 调用占比", f'sum by (openclaw_provider) ({model_calls})', grid(0, 35, 8, 8), "{{openclaw_provider}}", unit="short", decimals=0),
        bargauge(20, "Provider 成本", f'topk(15, sum by (openclaw_provider) ({costs}))', grid(8, 35, 8, 8), unit="currencyUSD", decimals=4, legend="{{openclaw_provider}}", color="orange"),
        timeseries(21, "模型 P95 时延", [prom(f'histogram_quantile(0.95, sum by (le, openclaw_provider, openclaw_model) ({rate("openclaw_model_call_duration_ms_milliseconds_bucket")}))', legend="{{openclaw_provider}} / {{openclaw_model}}")], grid(16, 35, 8, 8), unit="ms", decimals=2, legend="right"),
        table(22, "模型错误明细", f'sum by (gateway_id, openclaw_provider, openclaw_model, openclaw_error_category, openclaw_failure_kind) ({model_errors})', grid(0, 43, 24, 8), description="按龙虾、Provider、模型和官方错误分类汇总，可直接导出 CSV。"),

        row(23, "会话与队列 · 工作量、状态和恢复", 51),
        piechart(24, "区间会话状态事件", f'sum by (openclaw_state) ({increase("openclaw_session_state_total")})', grid(0, 52, 8, 8), "{{openclaw_state}}", decimals=0),
        timeseries(25, "会话轮次趋势", [prom(f'sum by (openclaw_channel, openclaw_trigger) ({rate("openclaw_session_turn_created_total")}) * 60', legend="{{openclaw_channel}} / {{openclaw_trigger}}")], grid(8, 52, 8, 8), unit="suffix: 轮次/分", decimals=2),
        table(26, "会话状态事件", f'sum by (gateway_id, openclaw_state, openclaw_reason) ({increase("openclaw_session_state_total")})', grid(16, 52, 8, 8)),
        timeseries(27, "队列深度均值", [prom(f'sum by (gateway_id, openclaw_lane) ({rate("openclaw_queue_depth_sum")}) / clamp_min(sum by (gateway_id, openclaw_lane) ({rate("openclaw_queue_depth_count")}), 0.000001)', legend="{{gateway_id}} / {{openclaw_lane}}")], grid(0, 60, 8, 8), decimals=2),
        timeseries(28, "队列等待 P95", [prom(f'histogram_quantile(0.95, sum by (le, gateway_id, openclaw_lane) ({rate("openclaw_queue_wait_ms_milliseconds_bucket")}))', legend="{{gateway_id}} / {{openclaw_lane}}")], grid(8, 60, 8, 8), unit="ms", decimals=2),
        table(29, "卡死与恢复事件", [
            prom(f'sum by (gateway_id, openclaw_state, openclaw_reason) ({increase("openclaw_session_stuck_total")})', ref_id="A", legend="卡死", instant=True, fmt="table"),
            prom(f'sum by (gateway_id, openclaw_state, openclaw_action, openclaw_status, openclaw_reason) ({increase("openclaw_session_recovery_completed_total")})', ref_id="B", legend="恢复", instant=True, fmt="table"),
        ], grid(16, 60, 8, 8)),

        row(30, "稳定性 · 双链路、错误、资源和遥测健康", 68),
        state_timeline(31, "OTel 数据活跃时间线", enrich(f'max by (gateway_id) (target_info{{{otel}}})'), grid(0, 69, 12, 9)),
        bargauge(32, "龙虾错误排名", f'topk(15, {enrich(f"sum by (gateway_id) ({model_errors})")})', grid(12, 69, 12, 9), unit="short", decimals=0, legend="{{lobster_name}}-{{server_display_id}}", color="red", gateway_links=True),
        bargauge(33, "RSS 内存均值排名", f'topk(15, {enrich(rss_average)})', grid(0, 78, 8, 8), unit="bytes", decimals=0, legend="{{lobster_name}}-{{server_display_id}}", color="cyan", gateway_links=True),
        table(34, "存活性告警", f'sum by (gateway_id, openclaw_liveness_reason) ({increase("openclaw_liveness_warning_total")})', grid(8, 78, 8, 8)),
        table(35, "遥测丢弃与导出状态", [
            prom(f'sum by (gateway_id, openclaw_diagnostic_async_queue_drop_class) ({increase("openclaw_diagnostic_async_queue_dropped_total")})', ref_id="A", legend="诊断队列丢弃", instant=True, fmt="table"),
            prom(f'sum by (gateway_id, openclaw_exporter, openclaw_signal, openclaw_status, openclaw_reason) ({increase("openclaw_telemetry_exporter_events_total")})', ref_id="B", legend="OTel Exporter", instant=True, fmt="table"),
            prom(f'max by (gateway_id) (up{{{native}}})', ref_id="C", legend="原生 Prometheus 直抓", instant=True, fmt="table"),
        ], grid(16, 78, 8, 8)),

        row(36, "龙虾运营索引 · 点击 Gateway ID 下钻", 86),
        table(
            37,
            "龙虾区间汇总",
            [
                prom(f'max by (gateway_id, lobster_name, department) (agentswatch_gateway_info{{{inventory}}})', ref_id="A", instant=True, fmt="table"),
                prom(f'max by (gateway_id) ({otel_active})', ref_id="B", instant=True, fmt="table"),
                prom(f'sum by (gateway_id) ({tokens_total})', ref_id="C", instant=True, fmt="table"),
                prom(f'sum by (gateway_id) ({model_calls})', ref_id="D", instant=True, fmt="table"),
                prom(f'sum by (gateway_id) ({session_turns})', ref_id="E", instant=True, fmt="table"),
                prom(f'sum by (gateway_id) ({model_errors})', ref_id="F", instant=True, fmt="table"),
                prom(f'sum by (gateway_id) ({costs})', ref_id="G", instant=True, fmt="table"),
            ],
            grid(0, 87, 24, 10),
            transformations=summary_transform(
                "gateway_id",
                {"gateway_id": "Gateway ID", "lobster_name": "龙虾名称", "department": "部门", "Value #A": "已纳管", "Value #B": "OTel活跃", "Value #C": "Token 总量", "Value #D": "模型调用", "Value #E": "会话轮次", "Value #F": "模型错误", "Value #G": "成本"},
                {"gateway_id": 0, "lobster_name": 1, "department": 2, "Value #A": 3, "Value #B": 4, "Value #C": 5, "Value #D": 6, "Value #E": 7, "Value #F": 8, "Value #G": 9},
                {"cluster": True},
            ),
            overrides=[
                value_override("Gateway ID", links=[gateway_data_link("${__value.raw}", "${cluster:raw}")]),
                value_override("Token 总量", "short", 0),
                value_override("模型调用", "short", 0),
                value_override("会话轮次", "short", 0),
                value_override("模型错误", "short", 0),
                value_override("成本", "currencyUSD", 4),
            ],
        ),
    ]
    variables = [
        datasource_var(),
        query_var("cluster", "集群", 'label_values(agentswatch_gateway_info{inventory_state="active"}, cluster)', multi=True),
        query_var("department", "部门", 'label_values(agentswatch_gateway_info{inventory_state="active",cluster=~"$cluster"}, department)', multi=True),
        query_var("region", "地域", 'label_values(agentswatch_gateway_info{inventory_state="active",cluster=~"$cluster"}, region)', multi=True),
        query_var("team", "团队", 'label_values(agentswatch_gateway_info{inventory_state="active",cluster=~"$cluster"}, team)', multi=True),
    ]
    return base_dashboard(
        "lobster-overview",
        "龙虾集群总览",
        "面向运维与运营的龙虾集群态势总览，所有累计值均随当前时间选择器变化。",
        ["openclaw", "lobster", "lobster-suite", "cluster"],
        panels,
        variables,
    )


def detail_dashboard():
    native = 'job="openclaw-gateways",cluster=~"$cluster",gateway_id=~"$gateway_id"'
    base = 'job="openclaw-gateway",gateway_id=~"$gateway_id"'
    scoped = base + ',openclaw_provider=~"$provider",openclaw_model=~"$model"'

    def increase(metric, matchers="", model_scope=False, period="$__range"):
        selector = scoped if model_scope else base
        return f'increase({metric}{{{selector}{matchers}}}[{period}])'

    def rate(metric, matchers="", model_scope=False):
        selector = scoped if model_scope else base
        return f'rate({metric}{{{selector}{matchers}}}[$__rate_interval])'

    tokens_total = increase("openclaw_tokens_total", ',openclaw_token="total"', model_scope=True)
    model_calls = increase("openclaw_model_call_duration_ms_milliseconds_count", model_scope=True)
    model_errors = increase(
        "openclaw_model_call_duration_ms_milliseconds_count",
        ',openclaw_error_category=~".+"',
        model_scope=True,
    )
    session_turns = increase("openclaw_session_turn_created_total")
    panels = [
        row(1, "龙虾核心态势 · 当前选定区间", 0),
        stat(2, "OTel 数据活跃", f'max(target_info{{{base}}}) or vector(0)', grid(0, 1, 6, 4), decimals=0, color="green"),
        stat(3, "Token 总量", f'sum({tokens_total}) or vector(0)', grid(6, 1, 6, 4), unit="short", decimals=0, color="purple"),
        stat(4, "输入 Token", f'sum({increase("openclaw_tokens_total", ",openclaw_token=\"input\"", model_scope=True)}) or vector(0)', grid(12, 1, 6, 4), unit="short", decimals=0, color="blue"),
        stat(5, "输出 Token", f'sum({increase("openclaw_tokens_total", ",openclaw_token=\"output\"", model_scope=True)}) or vector(0)', grid(18, 1, 6, 4), unit="short", decimals=0, color="cyan"),
        stat(6, "缓存 Token", f'sum({increase("openclaw_tokens_total", ",openclaw_token=~\"cache_read|cache_write\"", model_scope=True)}) or vector(0)', grid(0, 5, 6, 4), unit="short", decimals=0, color="green"),
        stat(7, "模型调用", f'sum({model_calls}) or vector(0)', grid(6, 5, 6, 4), unit="short", decimals=0, color="blue"),
        stat(8, "会话轮次", f'sum({session_turns}) or vector(0)', grid(12, 5, 6, 4), unit="short", decimals=0, color="cyan"),
        stat(9, "模型错误率", f'(sum({model_errors}) / clamp_min(sum({model_calls}), 1)) or vector(0)', grid(18, 5, 6, 4), unit="percentunit", decimals=2, color="red"),

        row(10, "Token 与成本", 9),
        piechart(11, "Token 构成", f'sum by (openclaw_token) ({increase("openclaw_tokens_total", ",openclaw_token=~\"input|output|cache_read|cache_write|reasoning\"", model_scope=True)})', grid(0, 10, 8, 8), "{{openclaw_token}}", unit="short", decimals=0),
        timeseries(12, "Token 消耗速率", [prom(f'sum by (openclaw_token, openclaw_provider, openclaw_model) ({rate("openclaw_tokens_total", ",openclaw_token=~\"input|output|cache_read|cache_write|reasoning\"", model_scope=True)})', legend="{{openclaw_token}} · {{openclaw_provider}}/{{openclaw_model}}")], grid(8, 10, 16, 8), unit="tps", decimals=2, stacking="normal"),
        bargauge(13, "模型 Token", f'topk(20, sum by (openclaw_provider, openclaw_model) ({tokens_total}))', grid(0, 18, 8, 8), unit="short", decimals=0, legend="{{openclaw_provider}} / {{openclaw_model}}", color="purple"),
        bargauge(14, "Provider Token", f'topk(15, sum by (openclaw_provider) ({tokens_total}))', grid(8, 18, 8, 8), unit="short", decimals=0, legend="{{openclaw_provider}}", color="cyan"),
        timeseries(15, "模型成本趋势", [prom(f'sum by (openclaw_provider, openclaw_model) ({rate("openclaw_cost_usd_total", model_scope=True)})', legend="{{openclaw_provider}} / {{openclaw_model}}")], grid(16, 18, 8, 8), unit="currencyUSD", decimals=6, legend="right", description="只使用 OpenClaw 实际导出的 cost。"),
        table(16, "Token 明细", f'sum by (openclaw_provider, openclaw_model, openclaw_token, openclaw_agent, openclaw_channel) ({increase("openclaw_tokens_total", model_scope=True)})', grid(0, 26, 24, 8), description="保留 total、prompt 以及全部 openclaw_token 供审计和 CSV 导出；构成图不会把包含关系重复相加。"),

        row(17, "模型调用与 Provider", 34),
        timeseries(18, "模型调用趋势", [
            prom(f'sum by (openclaw_provider, openclaw_model) ({rate("openclaw_model_call_duration_ms_milliseconds_count", model_scope=True)}) * 60', ref_id="A", legend="{{openclaw_provider}}/{{openclaw_model}}"),
            prom(f'sum by (openclaw_provider, openclaw_model) ({rate("openclaw_model_call_duration_ms_milliseconds_count", ",openclaw_error_category=~\".+\"", model_scope=True)}) * 60', ref_id="B", legend="错误 · {{openclaw_provider}}/{{openclaw_model}}"),
        ], grid(0, 35, 12, 8), unit="suffix: 次/分", decimals=2),
        bargauge(19, "模型调用次数", f'topk(20, sum by (openclaw_provider, openclaw_model) ({model_calls}))', grid(12, 35, 12, 8), unit="short", decimals=0, legend="{{openclaw_provider}}/{{openclaw_model}}", color="blue"),
        timeseries(20, "模型时延 P50 / P95", [
            prom(f'histogram_quantile(0.50, sum by (le, openclaw_provider, openclaw_model) ({rate("openclaw_model_call_duration_ms_milliseconds_bucket", model_scope=True)}))', ref_id="A", legend="P50 · {{openclaw_provider}}/{{openclaw_model}}"),
            prom(f'histogram_quantile(0.95, sum by (le, openclaw_provider, openclaw_model) ({rate("openclaw_model_call_duration_ms_milliseconds_bucket", model_scope=True)}))', ref_id="B", legend="P95 · {{openclaw_provider}}/{{openclaw_model}}"),
        ], grid(0, 43, 12, 8), unit="ms", decimals=2),
        table(21, "模型错误分类", f'sum by (openclaw_provider, openclaw_model, openclaw_api, openclaw_transport, openclaw_error_category, openclaw_failure_kind) ({model_errors})', grid(12, 43, 12, 8)),
        table(22, "模型故障转移", f'sum by (openclaw_provider, openclaw_model, openclaw_failover_to_provider, openclaw_failover_to_model, openclaw_failover_reason, openclaw_failover_suspended, openclaw_lane) ({increase("openclaw_model_failover_total")})', grid(0, 51, 24, 7)),

        row(23, "会话、队列与恢复", 58),
        piechart(24, "区间会话状态事件", f'sum by (openclaw_state) ({increase("openclaw_session_state_total")})', grid(0, 59, 8, 8), "{{openclaw_state}}", decimals=0),
        timeseries(25, "会话轮次趋势", [prom(f'sum by (openclaw_agent, openclaw_channel, openclaw_trigger) ({rate("openclaw_session_turn_created_total")}) * 60', legend="{{openclaw_agent}} · {{openclaw_channel}}/{{openclaw_trigger}}")], grid(8, 59, 8, 8), unit="suffix: 轮次/分", decimals=2),
        table(26, "会话状态事件", f'sum by (openclaw_state, openclaw_reason) ({increase("openclaw_session_state_total")})', grid(16, 59, 8, 8)),
        timeseries(27, "队列深度均值", [prom(f'sum by (openclaw_lane) ({rate("openclaw_queue_depth_sum")}) / clamp_min(sum by (openclaw_lane) ({rate("openclaw_queue_depth_count")}), 0.000001)', legend="{{openclaw_lane}}")], grid(0, 67, 8, 8), decimals=2),
        timeseries(28, "队列等待 P95", [prom(f'histogram_quantile(0.95, sum by (le, openclaw_lane) ({rate("openclaw_queue_wait_ms_milliseconds_bucket")}))', legend="{{openclaw_lane}}")], grid(8, 67, 8, 8), unit="ms", decimals=2),
        table(29, "卡死与恢复", [
            prom(f'sum by (openclaw_state, openclaw_reason) ({increase("openclaw_session_stuck_total")})', ref_id="A", legend="卡死", instant=True, fmt="table"),
            prom(f'sum by (openclaw_state, openclaw_action, openclaw_active_work_kind, openclaw_reason) ({increase("openclaw_session_recovery_requested_total")})', ref_id="B", legend="恢复请求", instant=True, fmt="table"),
            prom(f'sum by (openclaw_state, openclaw_action, openclaw_active_work_kind, openclaw_status, openclaw_reason) ({increase("openclaw_session_recovery_completed_total")})', ref_id="C", legend="恢复完成", instant=True, fmt="table"),
        ], grid(16, 67, 8, 8)),

        row(30, "运行、Harness、工具与技能", 75),
        timeseries(31, "Harness 运行趋势", [prom(f'sum by (openclaw_harness_id, openclaw_harness_plugin, openclaw_provider, openclaw_model, openclaw_outcome) ({rate("openclaw_harness_duration_ms_milliseconds_count", model_scope=True)}) * 60', legend="{{openclaw_harness_id}} · {{openclaw_provider}}/{{openclaw_model}} · {{openclaw_outcome}}")], grid(0, 76, 8, 8), unit="suffix: 次/分", decimals=2),
        timeseries(32, "Harness P95 时长", [prom(f'histogram_quantile(0.95, sum by (le, openclaw_harness_id, openclaw_provider, openclaw_model) ({rate("openclaw_harness_duration_ms_milliseconds_bucket", model_scope=True)}))', legend="{{openclaw_harness_id}} · {{openclaw_provider}}/{{openclaw_model}}")], grid(8, 76, 8, 8), unit="ms", decimals=2),
        timeseries(33, "消息处理趋势", [
            prom(f'sum by (openclaw_channel, openclaw_outcome) ({rate("openclaw_message_processed_total")}) * 60', ref_id="A", legend="处理 · {{openclaw_channel}}/{{openclaw_outcome}}"),
            prom(f'sum by (openclaw_channel, openclaw_outcome) ({rate("openclaw_message_dispatch_completed_total")}) * 60', ref_id="B", legend="分发 · {{openclaw_channel}}/{{openclaw_outcome}}"),
        ], grid(16, 76, 8, 8), unit="suffix: 次/分", decimals=2),
        table(34, "工具调用与拦截", [
            prom(f'sum by (gen_ai_tool_name, openclaw_tool_name, openclaw_tool_source, openclaw_tool_owner, openclaw_error_category) ({increase("openclaw_tool_execution_duration_ms_milliseconds_count")})', ref_id="A", legend="工具调用", instant=True, fmt="table"),
            prom(f'sum by (gen_ai_tool_name, openclaw_tool_name, openclaw_tool_source, openclaw_tool_owner, openclaw_denied_reason) ({increase("openclaw_tool_execution_blocked_total")})', ref_id="B", legend="工具拦截", instant=True, fmt="table"),
        ], grid(0, 84, 12, 8)),
        table(35, "技能使用", f'sum by (openclaw_skill_name, openclaw_skill_source, openclaw_skill_activation, openclaw_agent, openclaw_tool_name) ({increase("openclaw_skill_used_total")})', grid(12, 84, 12, 8)),

        row(36, "资源与遥测稳定性", 92),
        timeseries(37, "进程内存采样均值", [
            prom(f'sum({rate("openclaw_memory_rss_bytes_sum")}) / clamp_min(sum({rate("openclaw_memory_rss_bytes_count")}), 0.000001)', ref_id="A", legend="RSS"),
            prom(f'sum({rate("openclaw_memory_heap_used_bytes_sum")}) / clamp_min(sum({rate("openclaw_memory_heap_used_bytes_count")}), 0.000001)', ref_id="B", legend="Heap Used"),
            prom(f'sum({rate("openclaw_memory_heap_total_bytes_sum")}) / clamp_min(sum({rate("openclaw_memory_heap_total_bytes_count")}), 0.000001)', ref_id="C", legend="Heap Total"),
            prom(f'sum({rate("openclaw_memory_external_bytes_sum")}) / clamp_min(sum({rate("openclaw_memory_external_bytes_count")}), 0.000001)', ref_id="D", legend="External"),
            prom(f'sum({rate("openclaw_memory_array_buffers_bytes_sum")}) / clamp_min(sum({rate("openclaw_memory_array_buffers_bytes_count")}), 0.000001)', ref_id="E", legend="Array Buffers"),
        ], grid(0, 93, 8, 8), unit="bytes", decimals=0),
        timeseries(38, "事件循环与 CPU", [
            prom(f'histogram_quantile(0.95, sum by (le) ({rate("openclaw_liveness_event_loop_delay_p99_ms_milliseconds_bucket")}))', ref_id="A", legend="事件循环延迟 P95 (ms)"),
            prom(f'histogram_quantile(0.95, sum by (le) ({rate("openclaw_liveness_cpu_core_ratio_bucket")}))', ref_id="B", legend="CPU 核心占用 P95"),
        ], grid(8, 93, 8, 8), decimals=3),
        table(39, "存活性与内存压力", [
            prom(f'sum by (openclaw_liveness_reason) ({increase("openclaw_liveness_warning_total")})', ref_id="A", legend="存活性", instant=True, fmt="table"),
            prom(f'sum by (openclaw_memory_level, openclaw_memory_reason) ({increase("openclaw_memory_pressure_total")})', ref_id="B", legend="内存压力", instant=True, fmt="table"),
            prom(f'sum by (openclaw_payload_surface, openclaw_payload_action, openclaw_reason) ({increase("openclaw_payload_large_total")})', ref_id="C", legend="大载荷", instant=True, fmt="table"),
        ], grid(16, 93, 8, 8)),
        table(40, "遥测 Exporter 与丢弃", [
            prom(f'sum by (openclaw_exporter, openclaw_signal, openclaw_status, openclaw_reason, openclaw_error_category) ({increase("openclaw_telemetry_exporter_events_total")})', ref_id="A", legend="OTel Exporter", instant=True, fmt="table"),
            prom(f'sum by (openclaw_diagnostic_async_queue_drop_class) ({increase("openclaw_diagnostic_async_queue_dropped_total")})', ref_id="B", legend="诊断丢弃", instant=True, fmt="table"),
            prom(f'max(up{{{native}}})', ref_id="C", legend="原生 Prometheus 直抓", instant=True, fmt="table"),
        ], grid(0, 101, 24, 7)),
    ]
    variables = [
        datasource_var(),
        query_var("cluster", "集群", 'label_values(agentswatch_gateway_info{inventory_state="active"}, cluster)', multi=True),
        query_var("gateway_id", "龙虾", 'label_values(agentswatch_gateway_info{inventory_state="active",cluster=~"$cluster"}, gateway_id)', multi=False),
        query_var("provider", "Provider", 'label_values(openclaw_tokens_total{job="openclaw-gateway",gateway_id=~"$gateway_id"}, openclaw_provider)', multi=True),
        query_var("model", "模型", 'label_values(openclaw_tokens_total{job="openclaw-gateway",gateway_id=~"$gateway_id",openclaw_provider=~"$provider"}, openclaw_model)', multi=True),
    ]
    return base_dashboard(
        "lobster-gateway-detail",
        "龙虾实例详情",
        "按 gateway_id 下钻单只龙虾，覆盖 Token、模型、会话、运行、工具和稳定性。",
        ["openclaw", "lobster", "lobster-suite", "lobster-detail"],
        panels,
        variables,
    )


def report_dashboard():
    inventory = 'inventory_state="active",cluster=~"$cluster",gateway_id=~"$gateway_id",department=~"$department"'
    base = 'job="openclaw-gateway",gateway_id=~"$gateway_id"'
    scoped = base + ',openclaw_provider=~"$provider",openclaw_model=~"$model"'
    identity = f'max by (gateway_id) (agentswatch_gateway_info{{{inventory}}})'

    def selected(expr):
        return f'({expr}) and on (gateway_id) ({identity})'

    def increase(metric, matchers="", model_scope=False, period="$__range"):
        selector = scoped if model_scope else base
        return selected(f'increase({metric}{{{selector}{matchers}}}[{period}])')

    tokens_total = increase("openclaw_tokens_total", ',openclaw_token="total"', model_scope=True)
    input_tokens = increase("openclaw_tokens_total", ',openclaw_token="input"', model_scope=True)
    output_tokens = increase("openclaw_tokens_total", ',openclaw_token="output"', model_scope=True)
    cache_tokens = increase(
        "openclaw_tokens_total",
        ',openclaw_token=~"cache_read|cache_write"',
        model_scope=True,
    )
    reasoning_tokens = increase("openclaw_tokens_total", ',openclaw_token="reasoning"', model_scope=True)
    model_calls = increase("openclaw_model_call_duration_ms_milliseconds_count", model_scope=True)
    model_errors = increase(
        "openclaw_model_call_duration_ms_milliseconds_count",
        ',openclaw_error_category=~".+"',
        model_scope=True,
    )
    session_turns = increase("openclaw_session_turn_created_total")
    costs = increase("openclaw_cost_usd_total", model_scope=True)
    otel_active = selected(f'max by (gateway_id) (target_info{{{base}}})')
    gateway_identity_group = "gateway_id, lobster_name, department"
    gateway_metric_group = "gateway_id"
    gateway_metric_anchor = f'0 * max by ({gateway_metric_group}) (agentswatch_gateway_info{{{inventory}}})'

    def gateway_zero(expr):
        return f'({expr}) or ({gateway_metric_anchor})'

    gateway_targets = [
        prom(f'max by ({gateway_identity_group}) (agentswatch_gateway_info{{{inventory}}})', ref_id="A", instant=True, fmt="table"),
        prom(gateway_zero(f'max by ({gateway_metric_group}) ({otel_active})'), ref_id="B", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({tokens_total})'), ref_id="C", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({input_tokens})'), ref_id="D", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({output_tokens})'), ref_id="E", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({cache_tokens})'), ref_id="F", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({reasoning_tokens})'), ref_id="G", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({model_calls})'), ref_id="H", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({session_turns})'), ref_id="I", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({model_errors})'), ref_id="J", instant=True, fmt="table"),
        prom(gateway_zero(f'sum by ({gateway_metric_group}) ({costs})'), ref_id="K", instant=True, fmt="table"),
    ]
    gateway_rename = {
        "gateway_id": "Gateway ID", "lobster_name": "龙虾名称", "department": "部门",
        "Value #A": "已纳管", "Value #B": "OTel活跃", "Value #C": "Token 总量", "Value #D": "输入 Token",
        "Value #E": "输出 Token", "Value #F": "缓存 Token", "Value #G": "推理 Token", "Value #H": "模型调用",
        "Value #I": "会话轮次", "Value #J": "模型错误", "Value #K": "成本",
    }
    gateway_order = {name: idx for idx, name in enumerate([
        "gateway_id", "lobster_name", "department", "Value #A", "Value #B", "Value #C", "Value #D", "Value #E",
        "Value #F", "Value #G", "Value #H", "Value #I", "Value #J", "Value #K",
    ])}

    model_anchor = (
        f'0 * ((sum by (openclaw_provider, openclaw_model) ({model_calls})) or '
        f'(sum by (openclaw_provider, openclaw_model) ({tokens_total})))'
    )

    def model_key(expr):
        joined = (
            f'label_join(({expr}) or ({model_anchor}), "model_key", " / ", '
            '"openclaw_provider", "openclaw_model")'
        )
        return f'sum by (model_key) ({joined})'

    model_targets = [
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({model_calls})'), ref_id="A", instant=True, fmt="table"),
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({tokens_total})'), ref_id="B", instant=True, fmt="table"),
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({input_tokens})'), ref_id="C", instant=True, fmt="table"),
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({output_tokens})'), ref_id="D", instant=True, fmt="table"),
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({cache_tokens})'), ref_id="E", instant=True, fmt="table"),
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({reasoning_tokens})'), ref_id="F", instant=True, fmt="table"),
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({model_errors})'), ref_id="G", instant=True, fmt="table"),
        prom(model_key(f'sum by (openclaw_provider, openclaw_model) ({costs})'), ref_id="H", instant=True, fmt="table"),
    ]
    model_rename = {
        "model_key": "Provider / 模型", "Value #A": "调用次数", "Value #B": "Token 总量", "Value #C": "输入 Token",
        "Value #D": "输出 Token", "Value #E": "缓存 Token", "Value #F": "推理 Token", "Value #G": "模型错误", "Value #H": "成本",
    }
    model_order = {name: idx for idx, name in enumerate(["model_key", "Value #A", "Value #B", "Value #C", "Value #D", "Value #E", "Value #F", "Value #G", "Value #H"])}

    provider_anchor = f'0 * ((sum by (openclaw_provider) ({model_calls})) or (sum by (openclaw_provider) ({tokens_total})))'

    def provider_zero(expr):
        return f'({expr}) or ({provider_anchor})'

    provider_targets = [
        prom(provider_zero(f'sum by (openclaw_provider) ({model_calls})'), ref_id="A", instant=True, fmt="table"),
        prom(provider_zero(f'sum by (openclaw_provider) ({tokens_total})'), ref_id="B", instant=True, fmt="table"),
        prom(provider_zero(f'sum by (openclaw_provider) ({input_tokens})'), ref_id="C", instant=True, fmt="table"),
        prom(provider_zero(f'sum by (openclaw_provider) ({output_tokens})'), ref_id="D", instant=True, fmt="table"),
        prom(provider_zero(f'sum by (openclaw_provider) ({cache_tokens})'), ref_id="E", instant=True, fmt="table"),
        prom(provider_zero(f'sum by (openclaw_provider) ({model_errors})'), ref_id="F", instant=True, fmt="table"),
        prom(provider_zero(f'sum by (openclaw_provider) ({costs})'), ref_id="G", instant=True, fmt="table"),
    ]
    provider_rename = {
        "openclaw_provider": "Provider", "Value #A": "调用次数", "Value #B": "Token 总量", "Value #C": "输入 Token",
        "Value #D": "输出 Token", "Value #E": "缓存 Token", "Value #F": "模型错误", "Value #G": "成本",
    }
    provider_order = {name: idx for idx, name in enumerate(["openclaw_provider", "Value #A", "Value #B", "Value #C", "Value #D", "Value #E", "Value #F", "Value #G"])}

    numeric_overrides = [
        value_override("Token 总量", "short", 0), value_override("输入 Token", "short", 0),
        value_override("输出 Token", "short", 0), value_override("缓存 Token", "short", 0),
        value_override("推理 Token", "short", 0), value_override("调用次数", "short", 0),
        value_override("模型调用", "short", 0), value_override("会话轮次", "short", 0),
        value_override("模型错误", "short", 0), value_override("成本", "currencyUSD", 4),
    ]

    panels = [
        row(1, "报表摘要 · 当前时间选择器即报表周期", 0),
        stat(2, "Token 总量", f'sum({tokens_total}) or vector(0)', grid(0, 1, 4, 4), unit="short", decimals=0, color="purple"),
        stat(3, "输入 Token", f'sum({input_tokens}) or vector(0)', grid(4, 1, 4, 4), unit="short", decimals=0, color="blue"),
        stat(4, "输出 Token", f'sum({output_tokens}) or vector(0)', grid(8, 1, 4, 4), unit="short", decimals=0, color="cyan"),
        stat(5, "缓存 Token", f'sum({cache_tokens}) or vector(0)', grid(12, 1, 4, 4), unit="short", decimals=0, color="green"),
        stat(6, "推理 Token", f'sum({reasoning_tokens}) or vector(0)', grid(16, 1, 4, 4), unit="short", decimals=0, color="yellow"),
        stat(7, "模型调用", f'sum({model_calls}) or vector(0)', grid(20, 1, 4, 4), unit="short", decimals=0, color="blue"),
        stat(8, "会话轮次", f'sum({session_turns}) or vector(0)', grid(0, 5, 4, 4), unit="short", decimals=0, color="cyan"),
        stat(9, "模型成本", f'sum({costs}) or vector(0)', grid(4, 5, 4, 4), unit="currencyUSD", decimals=4, color="orange", description="只汇总 OpenClaw 实际导出的 cost，不建立推算账本。"),
        stat(10, "活跃龙虾", f'count(count by (gateway_id) (({session_turns}) > 0)) or vector(0)', grid(8, 5, 4, 4), decimals=0, color="green"),
        stat(11, "使用模型数", f'count(count by (openclaw_provider, openclaw_model) (({model_calls}) > 0)) or vector(0)', grid(12, 5, 4, 4), decimals=0, color="purple"),
        stat(12, "Provider 数", f'count(count by (openclaw_provider) (({model_calls}) > 0)) or vector(0)', grid(16, 5, 4, 4), decimals=0, color="blue"),
        stat(13, "模型错误", f'sum({model_errors}) or vector(0)', grid(20, 5, 4, 4), unit="short", decimals=0, color="red"),
        stat(14, "模型错误率", f'(sum({model_errors}) / clamp_min(sum({model_calls}), 1)) or vector(0)', grid(0, 9, 6, 4), unit="percentunit", decimals=2, color="red"),
        stat(15, "卡死会话", f'sum({increase("openclaw_session_stuck_total")}) or vector(0)', grid(6, 9, 6, 4), unit="short", decimals=0, color="red"),
        stat(16, "恢复动作", f'sum({increase("openclaw_session_recovery_completed_total")}) or vector(0)', grid(12, 9, 6, 4), unit="short", decimals=0, color="green"),
        stat(17, "OTel 活跃龙虾", f'count({otel_active}) or vector(0)', grid(18, 9, 6, 4), decimals=0, color="cyan"),

        row(18, "龙虾维度报表 · 可直接导出 CSV", 13),
        table(
            19,
            "龙虾运营汇总",
            gateway_targets,
            grid(0, 14, 24, 12),
            transformations=summary_transform("gateway_id", gateway_rename, gateway_order, {"cluster": True}),
            overrides=[value_override("Gateway ID", links=[gateway_data_link("${__value.raw}", "${cluster:raw}")]), *numeric_overrides],
        ),

        row(20, "周期趋势 · 每日增量", 26),
        timeseries(21, "每日 Token", [prom(f'sum by (openclaw_token) ({increase("openclaw_tokens_total", ",openclaw_token=~\"input|output|cache_read|cache_write|reasoning\"", model_scope=True, period="1d")})', legend="{{openclaw_token}}", interval="1d")], grid(0, 27, 12, 8), unit="short", decimals=0, stacking="normal"),
        timeseries(22, "每日模型调用与会话轮次", [
            prom(f'sum({increase("openclaw_model_call_duration_ms_milliseconds_count", model_scope=True, period="1d")})', ref_id="A", legend="模型调用", interval="1d"),
            prom(f'sum({increase("openclaw_session_turn_created_total", period="1d")})', ref_id="B", legend="会话轮次", interval="1d"),
            prom(f'sum({increase("openclaw_model_call_duration_ms_milliseconds_count", ",openclaw_error_category=~\".+\"", model_scope=True, period="1d")})', ref_id="C", legend="模型错误", interval="1d"),
        ], grid(12, 27, 12, 8), unit="short", decimals=0),

        row(23, "模型与 Provider 维度报表", 35),
        table(24, "模型运营汇总", model_targets, grid(0, 36, 24, 11), transformations=summary_transform("model_key", model_rename, model_order, {"openclaw_provider": True, "openclaw_model": True}), overrides=numeric_overrides),
        table(25, "Provider 运营汇总", provider_targets, grid(0, 47, 24, 10), transformations=summary_transform("openclaw_provider", provider_rename, provider_order), overrides=numeric_overrides),

        row(26, "异常与会话附表", 57),
        table(27, "模型错误附表", f'sum by (gateway_id, openclaw_provider, openclaw_model, openclaw_error_category, openclaw_failure_kind) ({model_errors})', grid(0, 58, 12, 10)),
        table(28, "会话状态附表", f'sum by (gateway_id, openclaw_state, openclaw_reason) ({increase("openclaw_session_state_total")})', grid(12, 58, 12, 10)),
        table(29, "工具与 Harness 附表", [
            prom(f'sum by (gateway_id, gen_ai_tool_name, openclaw_tool_name, openclaw_tool_source, openclaw_error_category) ({increase("openclaw_tool_execution_duration_ms_milliseconds_count")})', ref_id="A", legend="工具", instant=True, fmt="table"),
            prom(f'sum by (gateway_id, openclaw_harness_id, openclaw_harness_plugin, openclaw_provider, openclaw_model, openclaw_outcome, openclaw_error_category) ({increase("openclaw_harness_duration_ms_milliseconds_count", model_scope=True)})', ref_id="B", legend="Harness", instant=True, fmt="table"),
        ], grid(0, 68, 12, 9)),
        table(30, "稳定性附表", [
            prom(f'sum by (gateway_id, openclaw_liveness_reason) ({increase("openclaw_liveness_warning_total")})', ref_id="A", legend="存活性", instant=True, fmt="table"),
            prom(f'sum by (gateway_id, openclaw_state, openclaw_reason) ({increase("openclaw_session_stuck_total")})', ref_id="B", legend="卡死", instant=True, fmt="table"),
            prom(f'sum by (gateway_id, openclaw_state, openclaw_action, openclaw_status, openclaw_reason) ({increase("openclaw_session_recovery_completed_total")})', ref_id="C", legend="恢复", instant=True, fmt="table"),
        ], grid(12, 68, 12, 9)),
    ]
    variables = [
        datasource_var(),
        query_var("cluster", "集群", 'label_values(agentswatch_gateway_info{inventory_state="active"}, cluster)', multi=True),
        query_var("gateway_id", "龙虾", 'label_values(agentswatch_gateway_info{inventory_state="active",cluster=~"$cluster"}, gateway_id)', multi=True),
        query_var("department", "部门", 'label_values(agentswatch_gateway_info{inventory_state="active",cluster=~"$cluster"}, department)', multi=True),
        query_var("provider", "Provider", 'label_values(openclaw_tokens_total{job="openclaw-gateway",gateway_id=~"$gateway_id"}, openclaw_provider)', multi=True),
        query_var("model", "模型", 'label_values(openclaw_tokens_total{job="openclaw-gateway",gateway_id=~"$gateway_id",openclaw_provider=~"$provider"}, openclaw_model)', multi=True),
    ]
    return base_dashboard(
        "lobster-operations-report",
        "龙虾运营报表",
        "面向日、周、月、季度、年度和自定义区间的运营汇总，表格支持 Grafana CSV 导出。",
        ["openclaw", "lobster", "lobster-suite", "report"],
        panels,
        variables,
        links=[*nav_links(), *report_period_links()],
        time_from="now/M",
    )


def main():
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    dashboards = {
        "lobster-overview.json": overview_dashboard(),
        "lobster-gateway-detail.json": detail_dashboard(),
        "lobster-operations-report.json": report_dashboard(),
    }
    for filename, dashboard in dashboards.items():
        path = DASHBOARD_DIR / filename
        path.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
        print(path)


if __name__ == "__main__":
    main()
