# 本机部署与运维

正式实例 `vsa-local` 运行 Stage C 014，已应用 001～014。2026-11-26 维护门禁开放，支持普通任务和新 Bilibili 导入。默认模型为 Qwen3.8-Omni-Flash，可选 DeepSeek-V4.1-Flash。入口为 [127.0.0.1:58080](http://127.0.0.1:58080/)。部署使用 Docker Desktop 的 `desktop-linux` 上下文。Web 仅发布在本机，API、Worker、PostgreSQL 和 RustFS 通过 Docker 私网通信。开放前离线冒烟及实际开放检查见[正式验证记录](../docs/E2E_TEST_PLAN.md#stage-c-014-正式验证记录)。

## Stage C 014 正式运维

使用正式容器 Compose 标签、已核验的交付包 `manifest.json` 和仓库外私密配置确认项目、文件顺序、镜像和卷。正式交付配方依次为 `compose.wp07.yaml`、`compose.s3-12.yaml`、`compose.stage-c.yaml` 和 `compose.release.yaml`。Compose 显式指定 `--context desktop-linux`、项目 `vsa-local` 及获准的 `--env-file`，用 `COMPOSE_DISABLE_ENV_FILE=1` 禁用隐式 `.env`。仅无输出校验或在进程内核对渲染，避免记录插值后的私密配置。正式配置显式保存 `BILIBILI_IMPORT_ENABLED=true`。模型和 OSS 凭据仅传给 Worker，维护身份留在独立维护服务。当前 Worker 的普通 Qwen、Qwen 视觉和 DeepSeek 摘要时限分别为 600、300、3600 秒。

维护核对 001～014 账本、独立 journal 和 producer-ready、无待处理导入及未结清外部提交、五个原有数据卷和三个 Stage C 卷。Stage C 两个套接字卷和下载工作卷分别为 `bilibili-api`、`bilibili-egress` 和 `bilibili-work`。下载器无容器网络，出站服务使用专用桥接网，只有 Web 有宿主端口。按[维护门禁流程](#维护门禁与故障恢复)使用一次性维护服务执行 `status`，正式开放前另行批准并核对私密配置、镜像及控制身份。门禁切换无需运行迁移。

**014 兼容回退。** 已隔离演练的回退包仅用于 Bilibili 导入或下载器故障，尚未在正式实例切入。获准后先验证可信 CLOSED 并安全停止应用及下载器、出站和套接字初始化服务，保留 001～014 账本、数据、独立控制、全部八个卷和原有私网。使用回退包的已核验镜像和配方，省略 Stage C overlay 并确认新 Bilibili 导入关闭。开放前复核既有本地播放、Range、双语 Markdown、Bilibili 双语 Markdown 与原片/Range 拒绝，以及已受理的删除可正常推进。数据库、删除控制或共用 Worker 故障走[停止与恢复流程](#维护门禁与故障恢复)，按故障前滚或另行批准恢复。012 和仅识别 013 的交付包与 014 不兼容。

## 阶段 B 更新入口（仅限 012 实例）

以下入口仅用于经过核对的 012 实例。不得将它用于已提交 014 的正式实例，也不得重跑生产迁移。在适用的 012 实例中，确认应用已运行且上传、排队、模型处理和 OSS 清理均已结束，再从仓库根目录执行。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\Update-VsaLocal.ps1
~~~

入口默认使用本机 `release/s3-12-stage-b-all-sse-2026-11-15` 交付包及现有私密配置。交付包由 Git 忽略，需要单独提供。更新器核对配方、镜像和 001～012 账本指纹，暂时关闭维护门禁，按需替换 API、Worker 和 Web，再恢复原维护状态。PostgreSQL、RustFS 和五个正式数据卷继续使用既有资源。可用 `-ReleasePath` 指定已验证的兼容交付包，用 `-WhatIf` 预查入口和文件路径。

## 状态与凭据

~~~powershell
docker --context desktop-linux ps --filter 'label=com.docker.compose.project=vsa-local'
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:58080/api/health/ready
~~~

就绪检查正常为 `200`，维护关闭时业务和就绪请求返回 `503`，`/api/health/live` 仍可用于诊断。Windows 定时巡检和桌面通知处于禁用状态。当前配方来自已核验的 014 交付包。

## 交付包与资源保留

交付包的 `README.md` 说明如何启动隔离实例，`manifest.json` 记录镜像、归档和迁移 SHA-256，`verification/` 保存合成验证结果。双版本保留及清理审批按[仓库规则](../AGENTS.md#本机部署资源保留与退役)执行。正式原片、摘要、账号和删除控制按[删除与维护设计](../docs/design/DELETION_OPERATIONS.md)管理。

阶段 B 的正式 Web 曾复用上一版容器。其 Compose 标签当时指向旧包 `release/s3-12-stage-b-sse-2026-11-15` 中的 `deploy/compose.wp07.yaml` 和 `deploy/compose.s3-12.yaml`。014 正式部署已替换 Web。两份配置文件是否退役须先复查全部引用并另行授权。维护时以验证过的项目、配方和私有文件为准。

旧版本退役的逐项清单、实际空间及验证证据见本地归档入口 `docs/archive/stage-b-deepseek/README.md`。

## 首次创建与开放

现有正式 014 实例见[正式运维](#stage-c-014-正式运维)。以下步骤用于另行获准的全新 Stage C 实例。

1. 确认新项目名、空闲 IPv4 回环端口、新数据库与私有桶、五个应用命名卷、三个 Stage C 专用卷及部署负责人。以 [参数模板](wp07/.env.example)准备仓库外的私密环境文件，初始化、维护和运行密码分别生成。用 `docker compose config --quiet` 预查，限制私密文件访问。
2. 在隔离项目按[测试计划](../docs/E2E_TEST_PLAN.md#本机部署专项回归)复验候选，记录实际 API、Worker、Web、PostgreSQL、RustFS 和下载器镜像 ID 与冻结迁移。核对候选镜像、项目归属和端口，正式验收只用合成素材，真实模型另行配置。
3. 用[基础配方](compose.wp07.yaml)和 [Stage C 叠加配方](compose.stage-c.yaml)启动专用 DB 和对象存储并等待就绪。运行 `--profile initialize run --rm --no-deps volume-init`，再运行 `--profile initialize run --rm --no-deps initialize`。确认选定交付包的 001～014 数据库账本、表 owner、runtime ACL、独立 journal 身份、producer-ready 和 CLOSED。失败停在当前阶段并保留资源诊断。
4. 启动非 root API 和 Worker，保持 CLOSED。确认 live 可诊断，ready 和业务拒绝。获准后发布 Web 到 `127.0.0.1`，复核绑定、项目身份和巡检责任，再由维护通道执行 `open`。通过合成账户验证本人读取、元数据隔离、上传、双语导出、Range/seek 和删除，走正常删除清退测试素材。
5. 移交实际镜像、迁移指纹、门禁和巡检状态、DB/schema/桶、全部八个卷及责任人。批准后的正式配置和运行数据单独保管。

在仓库根目录为本次获准的目标指定 Compose 参数。后续命令统一在该项目下执行。

~~~powershell
$VsaCompose = @('--context', 'desktop-linux', 'compose',
    '--project-name', '<获准项目名>', '--env-file', '<仓库外的私密参数文件>',
    '-f', '.\deploy\compose.wp07.yaml', '-f', '.\deploy\compose.stage-c.yaml')
& docker @VsaCompose config --quiet
~~~

使用已核验的应用、Web、下载器及依赖镜像。在私密参数中设置 `WP07_APP_IMAGE`、`WP07_WEB_IMAGE` 和 `VSA_BILIBILI_IMAGE`，初始显式配置 `BILIBILI_IMPORT_ENABLED=false`。首次启动按顺序执行 `up -d --wait --pull never db object-store`、卷权限和数据库初始化命令。依赖及控制核验通过后，以 `up -d --no-deps --no-build --pull never api worker` 启动应用服务。CLOSED 阶段通过 live 检查，不等待业务就绪。获准发布时单独启动 Web，并在开门前验证回环绑定。启用新导入前，以 `up -d --no-build --pull never bilibili-downloader` 启动下载器及其出站、套接字初始化依赖，核对三个 Stage C 服务和专用卷，再持久化导入开关并让 API 重新加载。

## 阶段 B 与 012 回退

阶段 B 正式交付使用 001～012 账本，对所有视频时长使用 SSE，允许选择 DeepSeek-V4.1-Flash，以 Qwen3.8-Omni-Flash 为默认模型。该更新入口只核对 012 账本，新迁移须单独执行。012 的历史升级步骤和原始记录由本地 Stage B 归档索引保留，验证范围见[测试计划](../docs/E2E_TEST_PLAN.md#阶段-b-验证状态)。

**兼容回退。** 核对队列、导入、自有 OSS 的待清理资产和未知外部提交均已结清。保留 012 账本、数据库、RustFS 和五卷。先用同一更新入口加 `-WhatIf` 预查，获准正式切换后去掉该参数。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\Update-VsaLocal.ps1 -ReleasePath .\release\s3-12-stage-b-all-sse-rollback-2026-11-15 -WhatIf
~~~

该包保留 DeepSeek 选项、Qwen 默认值、旧任务和结果。DeepSeek 恢复为 60 分钟及以上使用 SSE，较短视频使用非流式。清理后已核对镜像和依赖指纹、Compose 配置、正式卷挂载及 12 项迁移账本，更新入口预演通过。这份留存包尚未实际切入正式实例。

**反馈与凭据。** 阶段 B 的 S3-12 启动器从仓库根 `.env` 将模型和 OSS 配置传给 Worker，将 `SUPPORT_EMAIL` 单独传给 API。留空关闭反馈入口，省略会保留已运行 API 的地址，API 仅公开合法的邮件地址。数据库和 RustFS 凭据由仓库外 `compose.env` 提供。012 更新器从 API 的 Compose 标签定位该文件，也可用 `-PrivateEnvPath` 指定。以交付包清单核验启动器和配方。

**DeepSeek 配置。** 标准入口 `Update-VsaLocal.ps1` 读取交付包清单的布尔值 `default_deepseek_enabled`，为正式包自动传入 `-EnableDeepSeek`，由启动器同时配置 API 和 Worker。当时的阶段 B 正式包和 012 回退包均为 `true`。入口使用仓库根 `.env` 作为 `-ModelEnvPath`。Qwen、DeepSeek 和 ASR 使用 Worker 的 `DASHSCOPE_API_KEY` 和千问兼容地址。DeepSeek 临时上传无需专用桶，自有 OSS 配置仍用于 Qwen 大视频与历史音轨清理。

**摘要等待与处置。** `DEEPSEEK_SUMMARY_TIMEOUT_SECONDS` 接受 1–3600 整数，默认 3600 秒，由 Worker 与 HTTP 读取共用。发布条件及传输实现见[架构说明](../docs/ARCHITECTURE.md#4-输入模型与输出流程)。断流或超时保留未知提交状态；账本仍为 `SUMMARY_SUBMITTING` 时，先按供应商调用记录和用量对账。`MODEL_OUTPUT_TRUNCATED` 表示供应商明确截断；`MODEL_INVALID_RESPONSE` 可用脱敏原因和请求 ID 摘要排查。平台临时音轨自然到期，与自有 OSS 清理及供应商处理副本保留分别处理。

## 维护门禁与故障恢复

对既有实例使用其实际 Compose 项目、配方和私密环境文件。受信部署者经独立维护通道执行 `--profile maintenance run --rm --no-deps maintenance python -m app.maintenance status`，按获准范围使用同一入口的 `close` 或 `open`。清理的 `status`、`health`、带版本的 `retry` 见[维护设计](../docs/design/DELETION_OPERATIONS.md)。

故障时置 CLOSED 并隔离受影响的 Web。若维护 CLI 因数据库或控制异常无法执行，停止已核实的入口并安全排空 API/Worker，保留正式卷和控制。依赖恢复后检查 journal、部署身份、producer-ready、失败任务、未完成 I/O 和清理定位。控制失信保持关闭。获准恢复先隔离重放删除并撤销旧会话，再开放。只回退到与迁移、所有者权限和独立控制兼容的已验证镜像，迁移失败按已提交登记前滚。停止实例时保留卷，禁止 `down --volumes`。

## 巡检与通知

启用前确认负责人、渠道、实际容器身份、IPv4 回环地址及 `/api` 路径。巡检覆盖 API、Worker、数据库、对象存储、清理进度和持久告警。默认 30 秒轮询、单轮最多 30 秒、持续故障阈值 60 秒。区分预期 CLOSED 和依赖故障。仅执行有界桶探测，事件记录固定故障类别，状态和事件文件留在仓库外。启用后验证故障、持续故障和恢复的实际投递。既有正式实例的 Windows 定时巡检及桌面通知已停用。

[千问与 OSS 操作指南](../docs/guides/QWEN_OSS.md)

## Stage C 轻量证据试点

Stage C 正式部署使用 014 账本。字幕优先，必要时使用独立 AAC 和一次 ASR。视觉路径可选。字幕对象和音轨文稿沿用任务的访问与删除周期，下载器使用独立工作卷。阶段 B 更新器只支持 012。已提交 014 的实例按[正式运维流程](#stage-c-014-正式运维)使用同账本的发布包或已验证的兼容回退。

## Stage C 第一阶段部署预备

`013_bilibili_input.sql` 将允许的输入类型从 LOCAL/URL 改为 LOCAL/BILIBILI，保留本地数据。任何历史 URL 行（含软删除）都会阻止迁移，须按已批准的数据保留和清退流程另行处理。 `014_bilibili_evidence.sql` 添加证据类型、可空 JSONB 文稿及约束。既有 LOCAL/BILIBILI 均获得 FULL_MEDIA 默认值，原结果无需重写。升级使用同一事务、迁移锁和精确文件账本。001～012 保持原始字节。核对[迁移及关键模块指纹](../docs/baseline/stage-c-014-fingerprints.json)。

## Stage C 第二阶段发布演练

以下流程用于独立演练。正式迁移和切换分别审批。

1. 从经核对的阶段 B API、Worker、Web 基础镜像构建候选，记录基础及交付镜像 ID、001～014 SQL 和关键模块哈希。Worker 基础镜像须提供 FFmpeg、FFprobe 和 DejaVu Sans。用锁定的 npm 依赖构建 `frontend/dist`。下载官方 yt-dlp wheel，按 `deploy/bilibili/requirements.txt` 校验，并与 API 依赖 wheel 放入仅构建使用的目录。构建用 `docker build --network none --build-context wheels=<wheel目录>`。API 和 Worker 用 `deploy/Dockerfile.stage-c-app` 和 `VSA_APP_BASE`。下载器用 `deploy/bilibili/Dockerfile` 和 `VSA_BILIBILI_BASE`。两种配方均使用 `--build-context app=backend/app`。Web 用 `deploy/Dockerfile.stage-c-web` 和 `VSA_WEB_BASE`。
2. 叠加 `compose.wp07.yaml`、需要的阶段 B 模型配置及 `compose.stage-c.yaml`，指定候选 API、Worker、Web 和 `VSA_BILIBILI_IMAGE`。在专用项目解析配置后用 `--no-build --pull never`。独立出站服务只给下载器提供受限出口。下载器无容器网络。只有 Stage C overlay 启用 Bilibili 导入。可设置 `BILIBILI_IMPORT_ENABLED=false` 暂停创建。演练使用模拟模型及合成素材，模型默认关闭。
3. 从 012 或 013 演练升级前，完成活动导入、任务、待清理转存及未知提交对账，关闭维护门禁并停 API、Worker、Web。保留数据库、对象存储和控制卷。用候选维护服务执行 `python -m app.stage_c_upgrade verify`，在进程内以 `MAINTENANCE_DATABASE_URL` 赋值给 `DATABASE_URL`。核对版本后运行一次 `apply`，再用 `verify` 确认 014。账本不符、遗留 URL 或在途记录会阻止应用迁移。新空库仍走 `app.deployment initialize`。保持 CLOSED 直至核对候选镜像、权限和账本。
4. 演练回退必须保留 014 及其数据。准备识别同一账本、能读取既有结果并处理 014 资产清理的兼容包，关闭新 Bilibili 导入，复核本地 Range、播放和双语 Markdown，复核 Bilibili 双语 Markdown 和原片/Range 404。012 包和只识别 013 的包不能接管 014；若缺少已验收的兼容包，保持 CLOSED，修复后前滚。回退不重写 SQL 账本或恢复旧删除控制。

下载器独立磁盘卷 `bilibili-work` 由 `bilibili-sockets` 初始化权限。启动时独占锁保护暂存回收。核对磁盘预算，并令外层反向代理等待至少 915 秒。轻量 API 时限为 240 秒；视觉导入上限为 256 MiB、900 秒。视觉摘要单次最多 300 秒、总计 1,200 秒，并受适配器及调度分配限制。Worker 的 Qwen 视觉配置 `QWEN_VISUAL_CALL_TIMEOUT_SECONDS` 默认 300 秒，独立于普通 Qwen 摘要配置。记录连接/TLS、发送、响应头、读流和清理。发送状态未明时禁止自动重新请求供应商。详见 [Stage C 容量评估](../docs/design/STAGE_C_LIMITS.md)。

### Stage C 正式发布清单

- 确认旧媒体直链入口关闭，清点 URL 行及关联内容。任何既有数据处置另行授权。
- 用隔离项目验证新建 014、012 升级、停用开关、同账本回退、删除与卷归属。核对实构镜像指纹并保留可用回退包。
- 对照 [E2E 计划](../docs/E2E_TEST_PLAN.md#stage-c-第二阶段验收)核对样本覆盖、已知质量限制和未验证的正式容量。发布与付费验证另行放行。
