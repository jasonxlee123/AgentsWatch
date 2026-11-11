# 批量服务器首次纳管

`agentswatch_api.bulk_server_onboarding` 用于把已入库但尚未配置连接的服务器，按
`internal_ip` 建立 SSH 管理连接，安装平台公钥与目标机 Python 3，并以仅公钥方式复验。
Ansible 运行在管理模块容器中，不需要在每台目标服务器安装 Ansible。

## 安全约束

- 计划文件不包含密码，固定以 `0600` 写入，并输出精确 SHA-256。
- 管理地址来自当前有效服务器的 `internal_ip`；重复、回环、未指定、组播或歧义数字地址会被拒绝或跳过。
- SSH host key 在计划阶段扫描；执行阶段必须显式接受，并再次扫描得到完全相同的结果。
- 执行前检查加密密钥、平台私钥、公钥导出、`sshpass`、Ansible Runner、已发布自动化版本和活动任务。
- 扫描完成后再次检查资产版本、IP 冲突和活动任务，再交互式输入并确认一次共享密码。
- 每台服务器创建独立的 `server.bootstrap_ssh` 单目标任务，失败互不阻塞。
- 首次上线应先生成少量 `--server-id` 试点计划；试点通过后重新生成全量计划，不得手工编辑计划 JSON。

## 试点计划

在管理 API 容器内运行，按需重复 `--server-id`：

```bash
python -m agentswatch_api.bulk_server_onboarding plan \
  --output /srv/agentswatch/management/bulk-onboarding/pilot-plan.json \
  --username openclaw \
  --port 22 \
  --credential-ref agentswatch-managed \
  --server-id <server-uuid-1> \
  --server-id <server-uuid-2> \
  --scan-workers 3
```

人工核对计划中的服务器、管理地址、host-key fingerprint、`actions` 和
`scan_failed`，记录命令输出的 `plan_sha256`。密码不得放入参数、环境变量、计划或日志。

## 确认执行

```bash
python -m agentswatch_api.bulk_server_onboarding apply \
  --plan-file /srv/agentswatch/management/bulk-onboarding/pilot-plan.json \
  --plan-sha256 <exact-sha256> \
  --accept-scanned-host-keys \
  --actor-ref <auditable-actor> \
  --workers 3
```

命令通过 TTY 两次读取同一密码。执行结束后应保存无秘密的 JSON 结果，并检查：

- `bootstrap_status='succeeded'`；
- `connection_status='reachable'`；
- `server.check_connection` 和 `server.collect_facts` 成功；
- 管理地址没有回环值，日常任务使用平台私钥而不是密码回退。

全量执行使用不带 `--server-id` 的新计划。失败目标应保留任务记录和错误状态，修复网络、
账号、sudo 或 Python 安装条件后重新生成计划再试；不要复用旧 SHA 或手工改写计划。
