# AgentsWatch 自动化库

此目录保存平台批准执行的标准化操作。资产和管理分组仍以 AgentsWatch 数据库为准，执行时按目标服务器动态生成 Ansible Inventory，不在这里维护第二份服务器清单。

当前操作：

- `server.bootstrap_ssh`：使用加密保存的登录密码首次建联，安装平台公钥和 Python 3，再以仅公钥方式复验并完成纳管；密码保留用于显式重新纳管，不作为日常任务的自动回退。
- `server.check_connection`：检查 Ansible 连接。
- `server.collect_facts`：采集服务器基础信息。
- `server.discover_runtimes`：发现 OpenClaw、Hermes 运行时。
- `runtime.collect_info`：只读采集实际版本、命令路径、配置/状态路径存在性和端点可用性，内容变化时写入新的不可变发现快照。
- `runtime.refresh_status`：刷新裸金属 systemd 系统服务、systemd 用户服务或 macOS launchd 运行时状态，并在配置端口后探测端点。
- `runtime.start` / `runtime.stop` / `runtime.restart`：执行标准运行时生命周期操作；配置了 `endpoint_port` 时，任务会等到端口真正可用或释放后才结束。
- `agent.apply_deployment`：把一个主智能体及其当前未退役子智能体部署或同步到受管的裸金属 OpenClaw 运行时；按运行时版本安装官方飞书插件、同步每个智能体的独立飞书账户和路由，并在配置校验后重启共享运行时。若 OpenClaw 要求确认插件能力，AgentsWatch 会保持失败关闭，操作者须先审阅能力声明并在目标运行时显式完成插件安装；平台不会自动同意未来新增的能力。首次执行成功后才创建平台部署关系，重复执行不会重复创建智能体或路由。历史导入关系的运行时 ID 必须由操作者明确填写并在远端验证存在，系统不会用显示名称猜测或创建一个同名替代品。
- `agent.migrate_deployment`：在目标 OpenClaw 运行时以禁用渠道完成预部署，再停用源端渠道、激活并验证目标端，最后原子切换平台关系；任何步骤失败都会保持原关系并尝试恢复源端、清理目标端。源端智能体身份与工作区始终保留，迁移过程中渠道会有短暂停机但不会双活。
- `agent.remove_deployment`：只移除由 AgentsWatch 管理的渠道账户和绑定，保留 OpenClaw 智能体身份与工作区；远端验证成功后才结束平台部署关系。若历史导入关系从未解析过运行时 ID、也从未由 AgentsWatch 管理，则仅以 CAS 结束平台关系，不伪装成远端清理。

`catalog.json` 是内置自动化的启动白名单。管理 API 启动时把这些定义及其内容哈希登记到数据库“自动化库”；后续在线创建的 Playbook 必须先保存为草稿、通过 Ansible 语法校验，再发布为不可变版本。执行目录只读取当前已发布版本。每个自动化声明稳定编码、说明、分类、语义版本、目标范围、风险、批量能力、超时和参数 Schema。调用方只能提交自动化编码和已声明参数，不能传入 Playbook 路径或任意 Shell。

在线管理的 Playbook 发布后物化到可变数据根的 `managed/`，数据库只保存稳定的 `managed/...` 逻辑路径，并保存完整版本内容、发布人、校验结果和 SHA-256。文件丢失时可从数据库中的已发布内容恢复；发布版本同时受到数据库不可变触发器保护。内置 Playbook 和 Role 始终从代码目录读取，不复制到可变数据目录。

服务器页面采用统一任务入口：勾选服务器后点击“执行任务”，从目录选择 Playbook、填写受控参数、确认下发并查看逐台结果。运行时页面提供同一执行层上的标准生命周期按钮；智能体详情中的“运行时部署”覆盖首次部署、同运行时同步、受控迁移和解除关系。迁移与解除会冻结源/目标运行时、OpenClaw 内部 ID 和主子成员清单，完整契约见[标准任务执行流程](../docs/automation-task-execution.md)。

退役子智能体会在下一次同步时解除飞书路由并归档部署成员，但保留 OpenClaw 工作区和运行时智能体，避免物理删除历史。存在尚未同步的新建或退役子体时，必须先同步当前部署，之后才能迁移或解除，以保证失败补偿与平台关系完全一致。

渠道 AppSecret 属于平台管理配置，不写入任务参数、数据库操作结果或 Ansible Inventory 明文。管理 API 只在执行前解密到控制端 `0600` 临时文件，Ansible 以 `no_log` 任务复制到远端临时文件并在配置合并后立即删除；控制端文件也会在 Runner 结束时删除。

本机验证：

```bash
./scripts/test_automation_local.sh
```

本机联调启动器会在检测到正在运行的 OpenClaw LaunchAgent 时，将开发机登记为一台裸金属服务器，并把 `ai.openclaw.gateway` 配置为 launchd 管理目标。所有状态刷新、启动、停止和重启仍通过上述目录和 `runtime_control` Role 执行，不调用页面传入的任意命令。

launchd 的停止流程会先禁用目标 Label 再发送终止信号，避免 `KeepAlive` 立即拉起进程；启动和重启会重新启用 Label。systemd 与 launchd 都以服务器本机回环地址探测运行时端口，防止服务管理器已报告运行、但 WebUI 尚未就绪时提前把任务标记成功。

Linux 运行时须按真实安装方式选择管理驱动：`systemd` 表示系统级 unit，启停和重启要求服务器连接启用 sudo；状态刷新和信息采集不提权。`systemd_user` 表示 SSH 登录用户自己的 unit，所有操作都禁止 become，并显式使用该用户的 `XDG_RUNTIME_DIR=/run/user/<uid>` 与 D-Bus 地址。服务器需为该用户启用 lingering，保证 `/run/user/<uid>/bus` 存在。运行命令优先使用资产中配置的绝对 `command_path`，为空时才从该登录用户的 PATH 发现，不硬编码运行账号。

手动执行本机连通性检查：

```bash
.venv-automation/bin/python scripts/run_automation_operation.py \
  server.check_connection \
  --targets tests/fixtures/automation/localhost.json
```

本机未配置时，可变数据根保持为仓库的 `automation/`，因此在线 Playbook、脱敏运行产物和 Runner 私有临时目录仍分别位于 `automation/managed/`、`automation/artifacts/` 和 `automation/.runner/`。部署环境通过一个绝对路径环境变量把三者迁移到持久化数据盘：

```bash
AGENTSWATCH_AUTOMATION_DATA_ROOT=/data/agentswatch/management/automation
```

配置后目录分别为 `$AGENTSWATCH_AUTOMATION_DATA_ROOT/managed/`、`artifacts/` 和 `.runner/`。服务账号必须可创建和读写这些目录；发布文件使用 `0600`，单次安全产物目录使用 `0700` 且脱敏 stdout 使用 `0600`，Runner 私有目录在任务收口后删除。Runner 子进程只接收 PATH、HOME、语言设置和执行所需的固定 Ansible 环境，不继承 API 数据库 DSN、加密密钥等秘密。管理 API 启动及每次执行前只清理带平台标记且锁已释放的陈旧 `.runner` 目录，不碰现有安全产物或无法确认归属的目录。相对路径配置会被拒绝，避免服务工作目录变化时把数据写到不可预期位置。

管理服务会保存自动化版本 ID、操作编码、冻结后的目标清单、参数、`语义版本+内容哈希` 和逐目标结果；任务中心基于这些记录统一检索服务器、运行时和智能体任务。

智能体部署在运行时状态目录的 `.agentswatch/agent-ownership.json` 中永久保留 OpenClaw key、主智能体与实际 workspace 的所有权记录。写入 ownership reservation 后，即使后续配置事务失败并回滚，也不会释放该 key；迁移或解除关系只清理平台管理的渠道配置，不删除智能体、workspace 或 ownership reservation。普通源端清理和补偿恢复必须验证全部 key 仍属于同一主智能体，只有尚未成功建立 ownership 的目标端预部署失败允许安全无操作补偿。
