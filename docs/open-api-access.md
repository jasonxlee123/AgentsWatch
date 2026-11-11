# 开放 API 访问契约

AgentsWatch 的管理台、外部调用方与 MCP 服务共用 `/api/v1` REST 业务入口。MCP 只做工具语义适配，不复制业务实现，也不直接访问数据库、Ansible 或 Shell。

## API Key

用户登录管理台后可管理自己的唯一有效 Key：

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/v1/auth/api-key` | 查询前缀、创建时间和最近使用时间，不返回密钥 |
| `POST /api/v1/auth/api-key` | 首次创建；完整 Key 只在本次响应返回 |
| `POST /api/v1/auth/api-key/rotate` | 刷新 Key；旧 Key 立即失效，完整新 Key 只返回一次 |
| `DELETE /api/v1/auth/api-key` | 撤销当前 Key |

Key 长期有效，不设置固定过期时间；停用用户、刷新或撤销都会立即使其失效。数据库仅保存 SHA-256 哈希和公开前缀。

外部请求使用标准 Bearer 头：

```http
Authorization: Bearer awk_<identifier>_<secret>
Accept: application/json
```

API Key 与所属用户共用角色权限：`admin` 可管理资产、任务、自动化库和用户，`operator` 可维护资产并执行任务，`viewer` 只读。服务端不因调用入口不同而创建第二套权限。

## 二次确认

需要确认的操作首次调用返回 HTTP 409：

```json
{
  "detail": {
    "code": "CONFIRMATION_REQUIRED",
    "message": "此操作需要二次确认",
    "confirmation_id": "cfm_xxx",
    "confirmation_header": "X-Confirmation-ID",
    "expires_at": "2026-07-16T12:00:00+00:00"
  }
}
```

调用方确认后，必须保持方法、路径、查询参数和请求体不变，并在有效期内重试：

```http
X-Confirmation-ID: cfm_xxx
```

确认只属于当前用户和当前请求，成功消费一次后不能重放。资产退役、Playbook 发布以及风险等级为 `medium` 或 `high` 的任务创建/重试均由服务端强制确认。

## 任务与审计

外部调用与管理台使用同一个任务接口：

- `GET /api/v1/automation/operations` 动态读取当前已发布 Playbook 契约；
- `POST /api/v1/automation/runs` 使用稳定操作编码、目标 UUID 和结构化参数创建任务；
- `GET /api/v1/automation/runs/{run_id}` 与 `/events` 查询状态、逐目标结果和 Ansible 执行过程。

任务的 `requested_by` 记录 API Key 所属用户名，因此 REST 与 MCP 发起的任务都能追溯到具体用户。真正的服务器、运行时与智能体操作仍只由已发布、受控的 Ansible Playbook 执行；接口不接受任意 Playbook 路径或 Shell 命令。
