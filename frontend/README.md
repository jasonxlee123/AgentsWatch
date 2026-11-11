# AgentsWatch 本机管理台

当前前端增量包含：

- 服务器 → 运行时 → 智能体关系总览。
- 近期任务执行与状态摘要。
- 服务器资产搜索、生命周期筛选、新增、修改、退役和详情。
- 服务器配置、IP、云环境、管理分组和承载运行时关系。
- 服务器首次纳管（密码建联、SSH 指纹确认、公钥安装与仅公钥复验）、单台/批量标准任务选择、目录驱动参数表单、确认下发和逐台操作结果。
- 运行时搜索、类型/部署方式筛选、新增、修改、退役和详情。
- 运行时宿主服务器、端点、路径、计划/实际版本、发现快照和活动主智能体关系。
- 裸金属运行时支持批量采集真实版本、命令路径、路径存在性和端点状态；发现快照仅在内容变化时新增。
- 裸金属运行时支持 Linux systemd 和 macOS launchd 状态刷新、启动、停止和重启；配置端口后，任务会等待端点真正可用或释放；容器仅保留资产选项，暂不提供操作。
- 运行时 WebUI 支持 SSH 临时隧道、Tailscale Serve 和私网直接地址，凭据加密保存并在新标签页打开原生界面。
- 智能体搜索、主子结构和个人/部门/业务类型筛选。
- 智能体归属、管理员、可访问用户、子智能体、运行时部署、管理分组和网络需求。
- 组织管理支持部门、人员档案的搜索、状态筛选、关系详情、新增、编辑和停用；部门编码与人员工号创建后不可修改。
- 人员档案与登录用户是两个独立模型，不会因创建、停用人员而自动创建账号或撤销会话。
- 服务器、运行时、智能体管理分组搜索、筛选和成员详情。
- 同一资源可加入多个静态管理分组，分组不表达高可用、主从或网络拓扑。
- Excel 批量导入服务器、运行时、智能体、部门和人员，提供模板下载、上传校验、变更预览和整批入库。
- 五类数据在同一事务内按依赖顺序提交；任一校验错误或并发版本冲突都会阻止整批写入。
- 账号密码登录、一次性滑块验证、会话退出和按角色裁剪操作入口。
- 每位用户可在顶栏创建、刷新、复制一次性展示或撤销自己的 API Key；Key 与账号角色共用服务端权限。
- 管理员可创建、停用用户并调整 `admin`、`operator`、`viewer` 角色或重置密码。

页面优先读取本机管理 API。API 未启动时使用确定性模拟数据，便于前端独立审阅；模拟模式不允许执行写操作。

## 本机联调运行

在项目根目录启动 PostgreSQL、管理 API、MCP 服务和前端：

```bash
./scripts/local_management_stack.sh start
```

启动器会打印实际前端、API 和 MCP 地址；端口被占用时会自动选择空闲端口。详见 [本机联调环境](../docs/local-management-stack.md)。
启动脚本会生成本机管理员密码，并输出登录用户名和 `0600` 密码文件路径；也可通过 `AGENTSWATCH_LOCAL_ADMIN_USERNAME` 和 `AGENTSWATCH_LOCAL_ADMIN_PASSWORD` 显式覆盖。凭据仅用于回环地址上的开发环境。

## 独立前端运行

```bash
npm install
npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

默认访问 `http://localhost:4173/`。Vite 根据 `AGENTSWATCH_LOCAL_API_PORT` 将 `/api` 和 `/healthz` 代理到本机 API，未设置时使用 `8000`。

## HTTPS 子路径部署

生产构建可以通过 `AGENTSWATCH_FRONTEND_BASE` 指定管理台子路径。官方管理镜像固定使用 `/agentswatch/`；仅管理模式由管理 Nginx 承载，联合模式由统一 Nginx 与 Grafana 共用 `443`：

```bash
AGENTSWATCH_FRONTEND_BASE=/agentswatch/ npm run build
```

构建后的静态资源会使用 `/agentswatch/` 前缀，前端请求也会自动从 `/api/...`、`/healthz` 改写为 `/agentswatch/api/...`、`/agentswatch/healthz`。生产 Nginx 把该前缀转发到 API 的 Unix socket；联合部署必须使用 `deploy/combined/nginx-combined.conf`，不要手工修改独立模式的 Nginx 配置。

未设置该变量时 `base` 保持 `/`，本机 `npm run dev` 和现有代理行为不变。

## 验证

```bash
npm test
npm run build
```

生产账号必须在部署时通过管理脚本创建，不能复用本机联调凭据。
