# 本机部署与运维

正式实例 `vsa-local` 运行 S3-12，入口为 [127.0.0.1:58080](http://127.0.0.1:58080/)。部署使用 Docker Desktop 的 `desktop-linux` 上下文。Web 仅发布在本机，API、Worker、PostgreSQL 和 RustFS 通过 Docker 私网通信。

## 正式更新入口

在仓库根目录打开 PowerShell。确认应用已运行，上传、排队、模型处理和 OSS 清理均已结束，再执行。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\Update-VsaLocal.ps1
~~~

入口默认使用本机 `release/s3-12-stage-b-all-sse-2026-11-15` 交付包及现有私密配置。交付包由 Git 忽略，需要单独提供。更新器核对配方、镜像和 001～012 账本指纹，暂时关闭维护门禁，按需替换 API、Worker 和 Web，再恢复原维护状态。PostgreSQL、RustFS 和五个正式数据卷继续使用既有资源。可用 `-ReleasePath` 指定已验证的兼容交付包，用 `-WhatIf` 预查入口和文件路径。

## 状态与凭据

~~~powershell
docker --context desktop-linux ps --filter 'label=com.docker.compose.project=vsa-local'
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:58080/api/health/ready
~~~

就绪检查正常为 `200`，维护关闭时业务拒绝访问。Windows 定时巡检和桌面通知处于禁用状态。当前配方位于 S3-12 交付包的 `deploy/`。

仓库根 `.env` 的模型和 OSS 配置仅传给 Worker。新版 S3-12 启动器将同一文件里的 `SUPPORT_EMAIL` 单独传给 API。留空可关闭反馈入口，省略该项会保留已运行 API 的地址。API 只公开通过校验的邮件地址。已有交付包的启动器受指纹保护，需要包含此修复的新交付包才能应用。数据库和 RustFS 凭据留在已有的仓库外 `compose.env`。更新入口从正式 API 容器的 Compose 标签解析后者；必要时可用 `-PrivateEnvPath` 指定原文件。两个文件都由本机负责人保管。

## 交付包与资源保留

交付包的 `README.md` 说明如何启动隔离实例，`manifest.json` 记录镜像、归档和迁移 SHA-256，`verification/` 保存合成验证结果。双版本保留及清理审批按[仓库规则](../AGENTS.md#本机部署资源保留与退役)执行。正式原片、摘要、账号和删除控制按[删除与维护设计](../docs/design/DELETION_OPERATIONS.md)管理。

当前正式 Web 复用了上一版容器。其 Compose 标签仍指向旧包 `release/s3-12-stage-b-sse-2026-11-15` 中的 `deploy/compose.wp07.yaml` 和 `deploy/compose.s3-12.yaml`。保留两个文件的原路径，等下一次获准的正式更新确实替换了 Web，再复查标签及文件引用。更新入口使用指定交付包及正式 API 标签记录的私有环境文件；维护时以验证过的项目、配方和私有文件为准。

旧版本退役的逐项清单、实际空间及验证证据见本地归档入口 `docs/archive/stage-b-deepseek/README.md`。

## 首次创建与开放

现有实例使用上方更新入口。以下步骤用于另行获准的全新实例。

1. 确认新项目名、空闲 IPv4 回环端口、新数据库与私有桶、五个新命名卷及部署负责人。以 [参数模板](wp07/.env.example)准备仓库外的私密环境文件，初始化、维护和运行密码分别生成。用 `docker compose config --quiet` 预查，限制私密文件访问。
2. 在隔离项目按[测试计划](../docs/E2E_TEST_PLAN.md#本机部署专项回归)复验候选，记录实际 API、Worker、Web、PostgreSQL 和 RustFS 镜像 ID 与冻结迁移。核对候选镜像、项目归属和端口，正式验收只用合成素材，真实模型另行配置。
3. 用 [Compose 配方](compose.wp07.yaml)启动专用 DB 和对象存储并等待就绪。运行 `--profile initialize run --rm --no-deps volume-init`，再运行 `--profile initialize run --rm --no-deps initialize`。确认选定交付包的数据库账本（现行包为 001～012）、表 owner、runtime ACL、独立 journal 身份、producer-ready 和 CLOSED。失败停在当前阶段并保留资源诊断。
4. 启动非 root API 和 Worker，保持 CLOSED。确认 live 可诊断，ready 和业务拒绝。获准后发布 Web 到 `127.0.0.1`，复核绑定、项目身份和巡检责任，再由维护通道执行 `open`。通过合成账户验证本人读取、元数据隔离、上传、双语导出、Range/seek 和删除，走正常删除清退测试素材。
5. 移交实际镜像、迁移指纹、门禁和巡检状态、DB/schema/桶、五卷及责任人。批准后的正式配置和运行数据单独保管。

在仓库根目录为本次获准的目标指定 Compose 参数。后续命令统一在该项目下执行。

~~~powershell
$VsaCompose = @('--context', 'desktop-linux', 'compose',
    '--project-name', '<获准项目名>', '--env-file', '<仓库外的私密参数文件>',
    '-f', '.\deploy\compose.wp07.yaml')
& docker @VsaCompose config --quiet
~~~

首次启动按顺序执行 `up -d --wait --pull never db object-store`、卷权限和数据库初始化命令。依赖及控制核验通过后，以 `up -d --no-deps --no-build --pull never api worker` 启动应用服务。CLOSED 阶段通过 live 检查，不等待业务就绪。获准发布时单独启动 Web，并在开门前验证回环绑定。

## 阶段 B 与 012 回退

正式 `vsa-local` 的迁移账本为 001～012。现行正式包对所有视频时长使用 SSE，DeepSeek-V4.1-Flash 常驻可选，Qwen3.8-Omni-Flash 是默认模型。正式更新入口只核对账本，新迁移须单独执行。012 的历史升级步骤和原始记录由本地 Stage B 归档索引保留，验证范围见[测试计划](../docs/E2E_TEST_PLAN.md#阶段-b-验证状态)。

**兼容回退。** 核对队列、导入、自有 OSS 的待清理资产和未知外部提交均已结清。保留 012 账本、数据库、RustFS 和五卷。先用同一更新入口加 `-WhatIf` 预查，获准正式切换后去掉该参数。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\Update-VsaLocal.ps1 -ReleasePath .\release\s3-12-stage-b-all-sse-rollback-2026-11-15 -WhatIf
~~~

该包保留 DeepSeek 选项、Qwen 默认值、旧任务和结果。DeepSeek 恢复为 60 分钟及以上使用 SSE，较短视频使用非流式。清理后已核对镜像和依赖指纹、Compose 配置、正式卷挂载及 12 项迁移账本，更新入口预演通过。这份留存包尚未实际切入正式实例。

**DeepSeek 配置。** 标准入口 `Update-VsaLocal.ps1` 读取交付包清单的布尔值 `default_deepseek_enabled`，为正式包自动传入 `-EnableDeepSeek`，由启动器同时配置 API 和 Worker。当前保留的两份交付包均为 `true`。入口使用仓库根 `.env` 作为 `-ModelEnvPath`。Qwen、DeepSeek 和 ASR 使用 Worker 的 `DASHSCOPE_API_KEY` 和千问兼容地址。DeepSeek 临时上传无需专用桶，自有 OSS 配置仍用于 Qwen 大视频与历史音轨清理。

**摘要等待与处置。** `DEEPSEEK_SUMMARY_TIMEOUT_SECONDS` 接受 1–3600 整数，默认 3600 秒，由 Worker 与 HTTP 读取共用。发布条件及传输实现见[架构说明](../docs/ARCHITECTURE.md#4-输入模型与输出流程)。断流或超时保留未知提交状态；账本仍为 `SUMMARY_SUBMITTING` 时，先按供应商调用记录和用量对账。`MODEL_OUTPUT_TRUNCATED` 表示供应商明确截断；`MODEL_INVALID_RESPONSE` 可用脱敏原因和请求 ID 摘要排查。平台临时音轨自然到期，与自有 OSS 清理及供应商处理副本保留分别处理。

## 维护门禁与故障恢复

对既有实例使用其实际 Compose 项目、配方和私密环境文件。受信部署者经独立维护通道执行 `--profile maintenance run --rm --no-deps maintenance python -m app.maintenance status`，按获准范围使用同一入口的 `close` 或 `open`。清理的 `status`、`health`、带版本的 `retry` 见[维护设计](../docs/design/DELETION_OPERATIONS.md)。

故障时置 CLOSED 并隔离受影响的 Web。若维护 CLI 因数据库或控制异常无法执行，停止已核实的入口并安全排空 API/Worker，保留正式卷和控制。依赖恢复后检查 journal、部署身份、producer-ready、失败任务、未完成 I/O 和清理定位。控制失信保持关闭。获准恢复先隔离重放删除并撤销旧会话，再开放。只回退到与迁移、所有者权限和独立控制兼容的已验证镜像，迁移失败按已提交登记前滚。停止实例时保留卷，禁止 `down --volumes`。

## 巡检与通知

启用前确认负责人、渠道、实际容器身份、IPv4 回环地址及 `/api` 路径。巡检覆盖 API、Worker、数据库、对象存储、清理进度和持久告警。默认 30 秒轮询、单轮最多 30 秒、持续故障阈值 60 秒。区分预期 CLOSED 和依赖故障。仅执行有界桶探测，事件记录固定故障类别，状态和事件文件留在仓库外。启用后验证故障、持续故障和恢复的实际投递。既有正式实例的 Windows 定时巡检及桌面通知已停用。

[千问与 OSS 操作指南](../docs/guides/QWEN_OSS.md)
