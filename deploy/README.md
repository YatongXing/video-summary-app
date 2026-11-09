# 本机部署与运维

正式实例 `vsa-local` 运行 S3-12，入口为 [127.0.0.1:58080](http://127.0.0.1:58080/)。部署使用 Docker Desktop 的 `desktop-linux` 上下文。Web 仅发布在本机，API、Worker、PostgreSQL 和 RustFS 通过 Docker 私网通信。

## 正式更新入口

在仓库根目录打开 PowerShell。确认应用已运行，上传、排队、模型处理和 OSS 清理均已结束，再执行。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\Update-VsaLocal.ps1
~~~

入口使用本机的 `release/s3-12-local` 交付包和现有私密配置路径。交付包由 Git 忽略，需要单独提供。更新器核对配方、镜像和 11 份迁移指纹，暂时关闭维护门禁，按需替换 API、Worker 和 Web，再恢复原维护状态。PostgreSQL、RustFS 和五个正式数据卷继续使用既有资源。变更根目录 `.env` 后使用同一命令应用。需要预查入口与文件路径时可加 `-WhatIf`。

## 状态与凭据

~~~powershell
docker --context desktop-linux ps --filter 'label=com.docker.compose.project=vsa-local'
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:58080/api/health/ready
~~~

就绪检查正常为 `200`，维护关闭时业务拒绝访问。Windows 定时巡检和桌面通知处于禁用状态。当前配方位于 S3-12 交付包的 `deploy/`。

仓库根 `.env` 供 Worker 读取模型和 OSS 配置，数据库和 RustFS 凭据留在已有的仓库外 `compose.env`。更新入口从正式 API 容器的 Compose 标签解析后者；必要时可用 `-PrivateEnvPath` 指定原文件。两个文件都由本机负责人保管。

## 验证与保留

交付包的 `README.md` 说明如何启动隔离实例，`manifest.json` 记录候选镜像、镜像归档和迁移 SHA-256，`verification/` 保存合成验证结果。既有实例已完成人工长视频验收，覆盖私有 OSS 路径。2 GB、2 小时、并发和模型质量各按对应场景验证。

软件包用于重建应用。业务原片、摘要、账号和删除控制保存在正式卷，按[删除与维护设计](../docs/design/DELETION_OPERATIONS.md)管理。历史副本和受保护证据按原保留规则处置。

## 首次创建与开放

现有实例使用上方更新入口。以下步骤用于另行获准的全新实例。

1. 确认新项目名、空闲 IPv4 回环端口、新数据库与私有桶、五个新命名卷及部署负责人。以 [参数模板](wp07.env.example)准备仓库外的私密环境文件，初始化、维护和运行密码分别生成。用 `docker compose config --quiet` 预查，限制私密文件访问。
2. 在隔离项目按[测试计划](../docs/E2E_TEST_PLAN.md#本机部署专项回归)复验候选，记录实际 API、Worker、Web、PostgreSQL 和 RustFS 镜像 ID 与冻结迁移。核对候选镜像、项目归属和端口，正式验收只用合成素材，真实模型另行配置。
3. 用 [Compose 配方](compose.wp07.yaml)启动专用 DB 和对象存储并等待就绪。运行 `--profile initialize run --rm --no-deps volume-init`，再运行 `--profile initialize run --rm --no-deps initialize`。确认迁移 001～011、表 owner、runtime ACL、独立 journal 身份、producer-ready 和 CLOSED。失败停在当前阶段并保留资源诊断。
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

## 维护门禁与故障恢复

对既有实例使用其实际 Compose 项目、配方和私密环境文件。受信部署者经独立维护通道执行 `--profile maintenance run --rm --no-deps maintenance python -m app.maintenance status`，按获准范围使用同一入口的 `close` 或 `open`。清理的 `status`、`health`、带版本的 `retry` 见[维护设计](../docs/design/DELETION_OPERATIONS.md)。

故障时置 CLOSED 并隔离受影响的 Web。若维护 CLI 因数据库或控制异常无法执行，停止已核实的入口并安全排空 API/Worker，保留正式卷和控制。依赖恢复后检查 journal、部署身份、producer-ready、失败任务、未完成 I/O 和清理定位。控制失信保持关闭。获准恢复先隔离重放删除并撤销旧会话，再开放。只回退到与迁移、所有者权限和独立控制兼容的已验证镜像，迁移失败按已提交登记前滚。停止实例时保留卷，禁止 `down --volumes`。

## 巡检与通知

启用前确认负责人、渠道、实际容器身份、IPv4 回环地址及 `/api` 路径。巡检覆盖 API、Worker、数据库、对象存储、清理进度和持久告警。默认 30 秒轮询、单轮最多 30 秒、持续故障阈值 60 秒。区分预期 CLOSED 和依赖故障。仅执行有界桶探测，事件记录固定故障类别，状态和事件文件留在仓库外。启用后验证故障、持续故障和恢复的实际投递。既有正式实例的 Windows 定时巡检及桌面通知已停用。

[千问与 OSS 操作指南](../docs/guides/QWEN_OSS.md)
