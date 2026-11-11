# 核心数据接入本机验证报告

> 文档状态：2026-07-12 阶段性验证快照，用于保留当时的测试证据，不代表当前完整产品能力。

## 1. 结论

核心数据接入与 Runtime 发现预览已经在本机完成原型验证。示例 `.xlsx` 文件可以解析、校验、生成字段级差异、转换为暂存对象并写入临时 PostgreSQL；本地测试清单可以生成迁移预览；OpenClaw 和 Hermes 模拟发现可以生成统一快照和 Agent 投影。

本阶段没有连接生产服务器、没有执行 SSH、没有修改任何生产清单，也没有向正式数据库提交数据。

验证日期：2026-07-12。

## 2. Excel 模板与解析

模板版本为 2.0，包含 `Instructions`、`_Meta`、`_Enums` 和 13 个数据 Sheet。模板元数据以 `_Meta.agentswatch_template_version` 为权威读取位置。

本机解析器已经实现：

- 只接受 ZIP 结构的 `.xlsx`。
- 拒绝宏部件和外部链接。
- 校验模板版本、必需 Sheet、固定表头和字段类型。
- UUID、枚举、布尔、正数、IP、端口和带时区时间校验。
- Primary/Subagent、服务类型和 Runtime 部署模式条件字段校验。
- 标识字段禁止公式。
- Secret 疑似内容阻断并在预览载荷中脱敏。
- 空单元格生成 `ignore`；`__CLEAR__` 生成明确 `clear`。
- 标准导入只按 platform_id 更新；initial_migration 才允许外部编码匹配。
- row_version 冲突、重复行、新增、修改和无变化分类。

## 3. 端到端暂存结果

本地样例工作簿包含：

- 1 条 Server 改名、内网 IP 修改和公网 IP 清空。
- 1 条新 Server。
- 1 条新 Business Primary Agent。
- 1 条新 OpenClaw baremetal Runtime。
- 1 条新 Agent 网络需求。

解析结果：

| 项目 | 数量 |
| --- | ---: |
| ImportBatch | 1 |
| ImportRow | 5 |
| ImportIssue | 0 |
| ImportChange | 69 |
| changed | 1 |
| new | 4 |

上述暂存对象已经转换为 SQL，并成功写入无网络、无持久卷的本机临时 PostgreSQL 16。测试结束后容器和数据自动删除。

## 4. 本地清单迁移预览

只读迁移预览继续保持以下结果：

- structurally_mappable=true。
- 拆分出的 Server、Runtime、Primary Agent、Deployment、Member 和 Snapshot 数量守恒。
- 不根据聚合计数伪造 Subagent。
- 待处理 warning 被显式保留。
- ready_to_commit=false，未写入主数据。

## 5. Runtime 模拟发现

本机使用两个 JSON Fixture 模拟只读发现响应：

| Runtime Family | Primary | Subagent | 结果 |
| --- | ---: | ---: | --- |
| OpenClaw | 2 | 1 | 统一投影成功 |
| Hermes | 1 | 1 | 统一投影成功 |

发现预览已经验证：

- 不同 Runtime Family 输出同一种框架无关 Agent 投影。
- 发现载荷可能报告多个 Primary，但资产提交必须按一个 Runtime 最多一个活动 Primary 的规则消歧或阻断。
- Subagent parent key 必须存在且指向 Primary。
- Runtime 内 key 重复会阻断。
- Token、API key 和 password 在配置摘要与原始载荷中被替换为 `[REDACTED]`。
- 内容哈希不包含发现时间；相同内容再次发现不生成新快照。
- 版本、配置或 Agent 内容变化会生成新哈希和新快照预览。
- 发现到的成员默认 mapping_status=unresolved，不按名称自动绑定 AgentAsset。

## 6. 本机运行

```bash
./scripts/test_core_data_ingest_local.sh
```

独立功能入口：

```bash
python3 scripts/preview_asset_import.py \
  --input tests/fixtures/core_asset_import_sample.xlsx \
  --master tests/fixtures/asset_import_master.json

python3 scripts/dry_run_core_asset_migration.py

python3 scripts/preview_runtime_discovery.py \
  --input tests/fixtures/runtime_discovery/openclaw_shared_runtime.json
```

## 7. 当前边界

以下内容没有在本阶段实现：

- Excel 批次的用户确认和正式主数据提交。
- 真实部门编码、人员工号和 Agent key 消歧。
- 真实 OpenClaw/Hermes 连接器、SSH 或 Runtime API 调用。
- 生产数据库、生产服务器、生产权限或网络验证。
- 管理 API 和管理界面。

核心只读 API 已完成[本机验证](core-read-api-local-validation.md)。下一阶段适合开发只消费只读 API 的最小管理台。
