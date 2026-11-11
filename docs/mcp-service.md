# AgentsWatch MCP 服务

MCP 服务是 AgentsWatch REST API 的无状态 HTTP 适配层。它不连接数据库，
不导入或调用 Ansible，也不提供 Shell 执行能力。用户 Bearer API Key 只在单次
请求上下文中原样转发给 AgentsWatch API，不写入配置、日志或持久化存储。

## 启动

```bash
python -m pip install -r requirements-mcp.txt
AGENTSWATCH_API_BASE_URL=http://127.0.0.1:8000 \
  python -m agentswatch_mcp
```

若 API 只监听 Unix socket，仍使用相同 HTTP/REST 调用语义：

```bash
AGENTSWATCH_API_BASE_URL=http://agentswatch \
AGENTSWATCH_API_UDS=/run/agentswatch/api.sock \
  python -m agentswatch_mcp
```

`AGENTSWATCH_API_BASE_URL` 继续决定请求 URL 和 `Host`；
`AGENTSWATCH_API_UDS` 只把 TCP 连接替换为 Unix socket 连接。

MCP 自身经 Unix socket 置于反向代理后面时，代理应把上游 `Host` 固定为
`127.0.0.1:<AGENTSWATCH_MCP_PORT>`（或 `localhost:<port>`），以满足 MCP SDK
默认启用的 DNS rebinding 防护。浏览器请求若带 `Origin`，代理也应将其固定为
对应的本地 MCP Origin；不要把任意公网 `Host` 或 `Origin` 原样传给 MCP 上游。

也可以直接启动 ASGI 应用：

```bash
uvicorn agentswatch_mcp.server:app --host 127.0.0.1 --port 8010
```

默认端点：

- MCP Streamable HTTP：`http://127.0.0.1:8010/mcp`
- 健康检查：`http://127.0.0.1:8010/healthz`

本机联调栈会自动启动该进程并打印实际端口；生产部署使用管理模块内部的
`mcp.sock`。仅管理模式由 `management-nginx` 暴露 `/agentswatch-mcp/mcp`；
联合模式由 `combined-nginx` 只读挂载 socket 并通过同一路径暴露。该 socket
不挂载给任何运维后端，MCP 和管理 API 都没有 published TCP 端口。

`/mcp` 的每个 HTTP 请求都必须携带：

```http
Authorization: Bearer <AgentsWatch API Key>
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENTSWATCH_API_BASE_URL` | `http://127.0.0.1:8000` | REST API 根地址，可包含部署前缀 |
| `AGENTSWATCH_API_UDS` | 未设置 | REST API 的绝对 Unix socket 路径；设置后不使用 TCP |
| `AGENTSWATCH_MCP_REQUEST_TIMEOUT` | `30` | 上游请求超时秒数 |
| `AGENTSWATCH_MCP_TLS_VERIFY` | `true` | 是否校验上游 TLS 证书 |
| `AGENTSWATCH_MCP_HOST` | `127.0.0.1` | `python -m` 的监听地址 |
| `AGENTSWATCH_MCP_PORT` | `8010` | `python -m` 的监听端口 |
| `AGENTSWATCH_MCP_LOG_LEVEL` | `info` | Uvicorn 日志级别 |

## 调用边界

资产工具只管理 Server、Runtime、Agent 和 Group 记录，不执行安装或部署。
`list_playbooks` 每次从 `/api/v1/automation/operations` 读取已发布操作，
`run_playbook` 使用通用运行接口，因此新增发布的 Playbook 不需要改动 MCP。

高风险调用若返回 `CONFIRMATION_REQUIRED`，工具结果会保留：

```json
{
  "ok": false,
  "error": {
    "status_code": 409,
    "code": "CONFIRMATION_REQUIRED",
    "message": "请确认本次操作",
    "confirmation": {
      "confirmation_id": "cfm_xxx",
      "expires_at": "2026-07-16T12:00:00Z"
    }
  }
}
```

客户端应保持目标和参数不变，并在同一个危险工具调用中传入
`confirmation_id`。适配器会将其转成 `X-Confirmation-ID` 请求头；有效性、
权限、过期和一次性使用全部由 AgentsWatch API 判断。
