# Windows 11 本地运行与人工验收指南

> **S3-10 历史运行与验收指南。** 原 Docker 项目及卷已退役。命令和回归口径保留供查阅。新的正式本机部署使用 [WP07-A 执行清单](../../CR-WP07A_FORMAL_RUNBOOK.md)，按阶段批准。

**轻量化基线（2026-11-04）：** L/R已批准，当前目标为UUID请求编号+任务版本CAS、两类DB权限、正常取消/task SH-EX候选、四态清理及三维护入口；010仅WP02，011后移WP04。[WP01当前契约](../cr/CR-WP01_CONTRACT.md)为目标，本指南S3-10操作仍属未切换实现，不代表新契约已通过，不执行本轮命令。

**版本提示（CR-001文档更新）：** 本指南A/B保留当前S3-10旧实现的可执行操作，B6的purpose授权、B7删除记录/人工清理等是尚未替换的现状，不是新产品基线。CR-001仅文档已更新，禁止照新规格猜测不存在的接口/请求生成命令。新人工验收见[CR-001_TEST_PLAN](../cr/CR-001_TEST_PLAN.md#6-新基线人工验收实施后才可执行)，须等实现及执行授权后再更新运行步骤。本轮不启动本指南或任何后续包。

适用版本：当前 S3-10 工作区（含尚未提交的 S3-07～10 文件），Windows 11、PowerShell、Docker Desktop/WSL2。目标是亲自打开**真实前端 + 真实 API + PostgreSQL + 私有对象存储 + 独立 Worker**，以离线模型替身验收流程。不是运行浏览器模拟测试，也不启动 S3-11/S3-12。

本文命令由用户逐步执行；本次文档任务没有替你启动应用、创建账户、迁移数据库、生成视频或清理数据。遇到失败先停在该步，不要继续粘贴后续命令。PowerShell 中原生命令退出码可用 `$LASTEXITCODE` 查看，0 表示该命令正常结束。

## 先了解本次运行边界

- 源码目录：`E:\GitHub\video-summary-app`。本机只需要 Git、Docker Desktop、兼容的 Node.js/npm；Python 3.11、FFmpeg 和后端依赖在容器内，主机 Python 非必需。
- 本文统一使用 Docker 项目名 **`video-summary-app-manual`**，复用现有 `deploy/compose.yaml`，不复制源码、不改架构。它创建自己的容器、私有网络和数据卷，避免误用原项目 `video-summary-app` 的旧数据。
- 此验收项目初次打开是空账户/空历史，**不代表原项目数据丢失**。以后继续验收必须使用相同项目名和配置。若你希望直接验收旧数据库，请先反馈，不要把命令中的项目名随意替换。
- API 启动会恢复过期导入暂存；Worker 启动会恢复租约，并按既有策略清理到期任务、日志和已请求删除的资料。即使 `WORKER_PROCESSOR_MODE=disabled`，也不能把它当成“禁用所有清理”的开关。因此隔离项目有实际意义；若该验收项目也已存有重要资料，先停止本次流程、核实保存情况。
- 使用 `deploy/.env.example` 的本地开发占位值，只允许本机回环访问；不要公开到家庭局域网或互联网。根目录真实 `.env` 不用于本次离线 Compose，**不要读取给别人看、覆盖、移动或提交它**。
- 离线指不调用外部推理；首次 Docker 构建、镜像拉取或 npm 安装仍可能需要联网下载依赖。

| 部件 | 实际入口 / 作用 |
| --- | --- |
| 网站 | `http://127.0.0.1:5173/`，Vite 开发服务器 |
| 后端 | `http://127.0.0.1:8000`，Compose 的 `api` |
| Worker | Compose 的 `worker`，没有独立网页端口 |
| PostgreSQL | Compose 的 `db`，容器内 5432；基础配置不开放主机端口 |
| 私有对象存储 | Compose 的 `object-store`，容器内 9000；基础配置不开放主机端口 |
| 前端代理 | 浏览器 `/api/*` 经 Vite 转发到本机 8000，并移除 `/api` 前缀 |

不要用 `npm run preview` 代替本指南：当前代理只写在 Vite 的开发服务器配置中。也不要把浏览器直接打开后端 8000 当成网站入口；后端没有托管前端首页。

## Part A：环境准备

### A1. 确认项目与未保存修改

**目的 / 预期：** 找到当前工作区，保留已有修改。**操作位置：** 新开 PowerShell。

```powershell
Set-Location -LiteralPath 'E:\GitHub\video-summary-app' -ErrorAction Stop
Get-Location
git status --short --branch
Test-Path -LiteralPath '.\deploy\compose.yaml'
Test-Path -LiteralPath '.\frontend\package-lock.json'
```

**成功判断：** 工作目录正确，两次路径检查均为 True；当前通常为 `implementation`，有未提交/未跟踪文件是已有进度，不是启动失败。

**常见问题：** 目录不存在先核实迁移位置；不要重新 clone 覆盖本工作区，不要通过重置/恢复命令清理这些修改。若本机尚未安装 Git，本步 Git 检查会失败，先完成 Git for Windows 安装后重开 PowerShell。

### A2. 检查 Docker、WSL2、Node 和可选 Python

**目的 / 预期：** Docker 同时有客户端和 Linux Server；Node 满足锁文件要求。**操作位置：** 项目根目录。

```powershell
git --version
docker version
docker compose version
wsl --status
node --version
npm.cmd --version
Get-Command py,python -ErrorAction SilentlyContinue | Select-Object Name,Source
```

**成功判断：** Docker Server 可访问且为 Linux 容器；Docker Desktop 使用 WSL2 引擎。已验证环境为 Docker 29.9.2 / Compose 5.6.1、Node 20.20.2。锁文件中的 Vite/React 插件要求 Node `^20.19.0 || >=22.12.0`；旧 Node 18 不适用。本机没有 Python 命令也可继续容器路线。

**常见问题：**

- Docker Server 连接失败：先打开 Docker Desktop，等待引擎启动；不要重新创建数据库或删除卷。
- PowerShell 找不到 Docker，但 Desktop 已安装：重开终端。此前本机安装在当前用户目录，可仅在当前窗口临时补 PATH：

```powershell
$dockerBin = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
if (Test-Path -LiteralPath $dockerBin) { $env:PATH = "$dockerBin;$env:PATH" }
docker version
```

- `npm.ps1` 被执行策略阻止：本指南使用 `npm.cmd`，不需要修改全局执行策略。
- WSL2/虚拟化尚未完成：先按 Docker Desktop 提示完成并重启，再继续。不要在引擎不可用时反复执行后续启动命令。

### A3. 核对配置、端口与数据隔离

**目的 / 预期：** 使用无模型密钥的示例，确认不会把旧项目卷拿来验收。**操作位置：** `deploy`。

```powershell
Set-Location -LiteralPath 'E:\GitHub\video-summary-app\deploy' -ErrorAction Stop
Test-Path -LiteralPath '.\.env.example'
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml config --quiet
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml config --services
docker volume ls --filter label=com.docker.compose.project=video-summary-app-manual --format '{{.Name}}'
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
Get-NetTCPConnection -State Listen -LocalPort 8000,5173 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

**成功判断：** 配置解析退出码为 0；服务为 `api`、`worker`、`db`、`object-store`。首次使用这个验收项目时，卷列表应为空，8000/5173 无其他程序占用。端口检查无结果是正常情况。

后续创建的卷名应以 `video-summary-app-manual_` 开头，包括 `postgres-data`、`object-data`、`input-staging`、`worker-processing`、`deletion-control`；它们不同于旧项目的 `video-summary-app_*`。

**常见问题：** 若这些验收卷已存在，先确认它们是谁创建、是否有重要资料；不要删除后重来。端口被占用先核实进程及其用途，不要强杀未知程序，也不要改为对外监听。不要载入 `compose.test.yaml`，人工运行不需要它开放的测试数据库端口。

环境准备不需要复制文件覆盖根目录 `.env`，也不需要填写 Qwen Key。PowerShell 中已有同名变量会优先于 env 文件；只检查以下不含秘密的模式值：

```powershell
Get-Item Env:WORKER_PROCESSOR_MODE,Env:BACKUP_INVENTORY_CONFIRMED,Env:AUTH_COOKIE_SECURE -ErrorAction SilentlyContinue
```

数据库/存储示例同样可能被当前终端的变量覆盖。下面只检查是否存在，不显示任何值：

```powershell
foreach ($name in 'PG_USER','PG_PASSWORD','PG_DB','OBJECT_ACCESS_KEY','OBJECT_SECRET_KEY') {
    if (Test-Path "Env:$name") { Write-Host "存在环境覆盖：$name（不显示值，先核实再继续）" }
}
```

本文数据库命令按示例的 `video_summary` 用户/库名编写；发现覆盖时先核实本机设置，不把真实值贴出来，不修改旧库来匹配示例。

预期是未设置，或分别与示例的 `offline`、`false`、`false` 一致。若你以前设置了其他值，先确认用途，不要盲目覆盖。不要运行完整环境变量列表或将完整 Compose 展开配置贴到聊天中。

### A4. 启动私有依赖并构建后端

**目的 / 预期：** 先准备 PostgreSQL/私有存储，再构建 API/Worker；此时不启动处理器。**操作位置：** `deploy`。

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml up -d db object-store
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml ps
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml build api worker
```

**成功判断：** `db`、`object-store` 最终为 healthy，构建成功。健康检查有启动等待，第一次不必立即变绿。

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml exec -T db pg_isready -U video_summary -d video_summary
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml exec -T db psql -U video_summary -d video_summary -tAc 'SELECT 1'
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml exec -T object-store curl -fsS http://127.0.0.1:9000/health
```

分别预期数据库接受连接、返回 1、对象存储探针退出码 0。不要从浏览器访问主机的 5432/9000/9001：这些端口没有映射。私有 bucket 会在首次成功写入视频时创建，空存储本身不是失败。

**常见问题：** 镜像/软件包下载失败先看 Docker Desktop 网络设置和错误文本；没有理由修改产品架构。数据库密码错误且已有卷时，不要改示例密码后期待数据库自动重置：PostgreSQL 初始化变量不会改写已有账户。保留现场并反馈。

### A5. 先迁移，再启动 API

**目的 / 预期：** 初始化/升级验收数据库，随后允许 API 启动重放删除记录。**操作位置：** `deploy`。

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml run --rm --no-deps api python -m app.migrate
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml up -d --no-deps api
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml ps
```

**成功判断：** 首次迁移列出 `001`～`009` 对应文件；重放显示 `Applied: none`。`run --rm` 只移除本次临时迁移容器，不移除数据库卷。迁移不是数据库重置，但会写入表/索引，因此此前必须完成数据隔离核对。

```powershell
(Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health/live' -TimeoutSec 10).StatusCode
(Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health/ready' -TimeoutSec 10).StatusCode
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml exec -T db psql -U video_summary -d video_summary -c 'SELECT version FROM schema_migrations ORDER BY version'
```

两个 HTTP 状态应为 200。`live` 只代表进程存活；`ready` 还核对数据库、存储和删除记录身份。网页“本地服务已连接”只探测 live，不能替代 ready。

**常见问题：** 迁移校验失败不要修改已应用迁移或删库；API 启动时出现 deletion journal/identity 错误不要删 `deletion-control` 卷修复。先查看：

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml logs --tail 80 api
```

只反馈相关错误，排除密钥、Cookie、内容正文。当前 `APP_LOG_DIR` 没有接入 Compose/文件日志输出，不能期待根目录配置让日志自动出现在 E 盘文件夹；以 Docker 日志为准。

### A6. 在交互终端创建验收管理员

**目的 / 预期：** 在验收库创建你自己知道密码的管理员，不使用硬编码默认密码。**操作位置：** 正常交互式 PowerShell，`deploy`；API 已运行。

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml exec api python -m app.admin create-admin --email local-admin@example.test
```

**成功判断：** 两次隐藏密码提示一致（8～128 字符），输出 `Account operation completed: ...`。请自行保存该密码；管理员身份将在 B6 登录后确认。邮箱仅为本地验收标识，MVP 无邮箱验证。

**常见问题：** 不要在该命令中添加 `-T`，不要通过管道输入密码，也不要把密码写在命令行/文档中。Compose exec 默认分配 TTY；若提示需要交互终端，改在本机普通 PowerShell 直接执行。同名账户存在会拒绝，不会提升普通账户权限；如果已有管理员则使用原凭据，或者另选一个未使用邮箱，不删旧账户。此实际交互过程留给你现场验收，本轮未替你创建管理员。

## Part B：离线模式本地人工验收

### B1. 启动前端，先保留 Worker 未启动

**目的 / 预期：** 先看真实网站和排队行为；提交任务时还不扣机会。**操作位置：** 另开 PowerShell 窗口，进入 `frontend`。

```powershell
Set-Location -LiteralPath 'E:\GitHub\video-summary-app\frontend' -ErrorAction Stop
npm.cmd ci
npm.cmd run build
npm.cmd run dev -- --port 5173 --strictPort
```

**成功判断：** 终端显示本机 5173 地址并保持运行，浏览器打开 `http://127.0.0.1:5173/`。此窗口不要关闭；另一个 `deploy` 窗口用于 Docker 命令。

**常见问题：** `npm ci` 根据现有锁文件重新安装 node_modules，不改业务数据；遇到锁文件不一致先反馈，不为消除报错改用更新依赖。5173 被占用时 strictPort 会明确失败，避免 Vite 自动改为 5174 后触发 Origin 拒绝。一直使用同一 `127.0.0.1` 地址，不来回换 localhost 导致 Cookie 不一致。若 403，检查端口/Origin/会话，不关闭权限检查。

本步不用主机 `pip install`、本地 Python 启动后端或另装 FFmpeg。不要启动第二份后端占用 8000。

### B2. 注册、登录、退出再登录

**目的 / 预期：** 检查数据库账户与会话。**操作位置：** 浏览器；可复制以下命令打开正确入口。

```powershell
Start-Process 'http://127.0.0.1:5173/'
```

点击“注册账户”，使用新的 `manual-user@example.test` 和自行设置的密码；注册后再登录。检查个人账户与初始 3 次机会；退出后重新输入密码登录。不要把管理员邮箱通过普通注册界面重新注册。

**成功判断：** 普通账户没有管理员工作台；退出后回到登录界面；已注册凭据可再次使用。页面会明确显示离线流程验证模式。

**常见问题：** 相同邮箱重复注册被拒是正常行为。密码错误、认证限速和注册限速不会因重启 API 自动清零；不要反复点击。首轮默认每账号 15 分钟 5 次失败、每来源 20 次失败，各暂停 15 分钟；同一 Vite 代理后的浏览器共享来源限速。停止容器不等于退出登录，有效 Cookie/8 小时服务端会话可能仍在；验证重新登录请主动退出。

### B3. 生成并上传新的合成视频

**目的 / 预期：** 不使用私人素材，在 API 容器生成 5 秒 H.264/AAC MP4，复制到 Windows 临时目录；不覆盖已有文件。**操作位置：** `deploy` 的 PowerShell。

```powershell
Set-Location -LiteralPath 'E:\GitHub\video-summary-app\deploy' -ErrorAction Stop
$sampleTag = [Guid]::NewGuid().ToString('N')
$containerFile = "/tmp/manual-$sampleTag.mp4"
$sampleDir = Join-Path $env:TEMP 'video-summary-app-manual'
New-Item -ItemType Directory -Path $sampleDir -Force | Out-Null
$samplePath = Join-Path $sampleDir "manual-$sampleTag.mp4"
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml exec -T api ffmpeg -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=320x180:rate=10:duration=5' -f lavfi -i 'sine=frequency=440:duration=5' -c:v libx264 -threads 2 -pix_fmt yuv420p -c:a aac -shortest $containerFile
if ($LASTEXITCODE -ne 0) { throw '合成视频生成失败，先不要继续上传。' }
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml cp "api:$containerFile" "$samplePath"
Get-Item -LiteralPath $samplePath | Select-Object FullName,Length
Start-Process explorer.exe -ArgumentList $sampleDir
```

浏览器选“本地文件”→选择该文件→确认处理授权→“导入并校验”。文件虽是合成，仍须明确勾选授权。

**成功判断：** 校验通过，真实任务列表出现排队任务；Worker 尚未启动时保持 Queued/排队中，额度仍为 3。可选再上传一条专门测试任务，在排队时点“取消任务”，确认 Cancelled 且未扣机会。不要操作旧资料。

**常见问题：** 若 Worker 已在运行，短任务可能很快完成，这是状态差异而非扣额错误；首次验收需按顺序，重跑则参考 B8。当前导入成功提示有“处理与总结功能将在后续工作包接通”的过时文字，**以任务列表为准**：离线闭环已接通，本轮仅报告未修改这段 UI。找不到 FFmpeg 说明 API 镜像过旧，回到构建/迁移步骤，不在主机临时换实现。

### B4. 启动 Worker，观察状态和配额

**目的 / 预期：** 让排队任务走真实预处理、离线替身和输出校验。**操作位置：** `deploy`。

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml up -d --no-deps worker
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml ps
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml exec -T worker python -m app.worker --healthcheck
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml logs --tail 60 worker
```

**成功判断：** Worker healthy，健康命令退出码 0；任务由排队到处理中再到已完成，只有首次开始扣 1 次，普通账户余额应为 2（若你另有实际处理任务则按实际计算）。成功更新页面，不弹窗。任务列表约每 15 秒刷新，也可点该区域“刷新”。

短合成任务可能在两次刷新间完成，无法肉眼看到 Processing 并不证明漏掉处理；查看提交/处理时间及结果。Processing 使用不确定进度条，没有精确 ETA；60 分钟视频的 20 分钟提示是目标，不是本次验收成绩。

**常见问题：** 长期 Queued 先检查 Worker 是否 healthy/模式是否 offline、队首用户额度及冷却。只看到 api healthy 不等于 Worker 已启动。不要通过改表或跳过 FIFO 强行处理。Failed 时记录错误分类、退款与重试资格；内部错误应退款，同任务重试不另扣。离线模式没有真实 API 费用。

### B5. 查看中英文结果、原片与导出

**目的 / 预期：** 验收真实系统输出门槛和浏览器体验。**操作位置：** 网站“我的视频任务”→“查看结果”。

查看总览、要点、章节、关键时刻、局限；切换中文/English，分别点击“导出 JSON”。播放合成原片、点击章节/事件时间按钮，记录浏览器是否正确跳转。替身事件通常在 0 秒，不能据此证明任意时间点定位精度。

下载后可在 PowerShell 进行只读解析（路径通过提示输入，不读取 `.env`）：

```powershell
$jsonPath = Read-Host '请输入刚下载的 JSON 完整路径（不是 .env）'
$document = Get-Content -LiteralPath $jsonPath -Raw | ConvertFrom-Json
$document | Select-Object schema_version,language
$document.video | Select-Object filename,duration_seconds
$document.key_events | Select-Object timestamp_seconds,title
```

**成功判断：** 两种输出均能解析，`schema_version=1.0`、语言为 zh/en、文件名不带本地完整路径，事件有时间戳且有序。导出不得增加用户/任务 ID、存储位置、完整逐字稿等字段。页面应明确离线/OFFLINE TEST；出现通用合成文字而不复述画面是当前正确行为。

**常见问题：** 视频不播放请记录浏览器/错误信息；摘要仍可读取不代表播放已通过。JSON 下载被拒要记录任务状态，不手工拼装假结果。真实音画理解、翻译质量、长视频时间戳准确性和 P90 都不由此证明。Chrome 模拟接口回归未验证实际媒体解码，这次播放体验需要你亲自确认。

### B6. 检查输入失败与管理员功能（S3-10旧实现）

**目的 / 预期：** 看清输入拒绝和任务失败的差别，核对现有管理入口。**操作位置：** `deploy` 与浏览器。

先创建一个全新伪装 MP4，用于“导入失败”检查：

```powershell
$invalidPath = Join-Path $env:TEMP ("manual-invalid-" + [Guid]::NewGuid().ToString('N') + '.mp4')
Set-Content -LiteralPath $invalidPath -Value 'synthetic invalid media, not a video' -NoNewline
Get-Item -LiteralPath $invalidPath | Select-Object FullName,Length
```

上传该文件并确认授权，预期拒绝、无新处理任务、不扣机会。**输入校验拒绝不是历史中的 Failed 处理任务**。若无自然处理故障，可以把 Failed/重试的手动观察记为“未观察”，不为制造故障破坏存储、修改数据库或强杀进程；对应故障注入已有自动回归证据。

再用独立浏览器隐私窗口登录 A6 的管理员。默认可看按用户分组/筛选的任务元数据、时间、错误、重试、额度和模型调用元数据；不是查看所有人摘要的超级内容权限。用户 ID 可从管理员分组标题获取。

所有者在普通窗口对**新建测试任务**点击“排障授权”，填 `manual-check`、选择“摘要”并确认。管理员填相同目的后点击“请求查看授权摘要”，应可查看；这不自动授予原视频访问。撤销后管理员重新请求应被拒。若测试视频访问，要单独授予 video 范围，再按相同用途请求。已经渲染或下载的副本不会从管理员设备上被收回，检查的是后续访问。

HTTPS 链接入口首轮只接受有处理授权的公网 HTTPS 443 直达视频文件；本机/内网 URL 和平台播放页面被拒是预期。本次人工验收不要求寻找外部视频，也不要为测试公开本地视频目录。

**成功判断：** 元数据默认可见，未授权正文不可见；指定用途/范围授权后允许，撤销后拒绝。后台有清理告警/人工推进区域；全新项目没有告警是正常情况，不为填满界面制造失败。

**常见问题：** 管理员同浏览器标签切换会共享 Cookie，建议独立隐私窗口避免覆盖普通账户会话。若无法登录管理员，先确认创建账户的 Docker 项目名相同；普通注册不会创建管理员。这里没有邮箱审核、密码找回、任意额度修改或完整运营控制台。

### B7. 可选：只删除本次新建的测试任务（S3-10旧实现）

**目的 / 预期：** 用户自行验证删除反馈，**此步骤会实际清除所选任务的数据**。**操作位置：** 网站；只有确认目标是本次合成测试任务后才操作。

点“删除任务”并确认，检查立即从历史消失，“删除记录”先显示待清理，之后在线清理完成。未核验备份保持 PENDING/尚未确认，不是全部删除成功。管理员“一键强制删除”实际是审计式重新推进，不是跳过清理验证。

只读查看后台：

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml logs --tail 60 worker
```

**成功判断：** 删除前没有提前宣称成功；之后无法重新查看/导出该任务；在线/备份状态分开。处理中删除可能等待原租约安全结束再清理。

**常见问题：** 尚未确认的备份不要靠把 `BACKUP_INVENTORY_CONFIRMED` 改为 true 来消除提示。S3-12现按CR-D09/10改为历史副本清点隔离核实和获批退役、避免新内容备份及软件重建/不可恢复风险交付；旧实现仍保留必要清理和防复活，不在本指南执行。若不希望删除任何测试资料，可跳过本步，记为“未执行”。

### B8. 安全停止与下次继续

**目的 / 预期：** 保留账号、视频、结果和删除记录卷。**操作位置：** 浏览器、前端窗口和 `deploy`。

先等当前测试完成，若需验证下次重新输密码则先退出登录。前端窗口按 Ctrl+C。随后在 `deploy` 执行：

```powershell
Set-Location -LiteralPath 'E:\GitHub\video-summary-app\deploy' -ErrorAction Stop
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml stop worker
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml stop api db object-store
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml ps -a
```

**成功判断：** 验收项目容器停止，卷保留。不要执行卷删除、系统 prune、数据库重置或恢复旧快照来“清理测试”。停止中的任务可能按内部失败/租约恢复处理；这不等于取消或正常完成。

再次运行时在同一目录、同一项目名使用：

```powershell
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml up -d db object-store
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml ps
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml run --rm --no-deps api python -m app.migrate
docker compose --project-name video-summary-app-manual --env-file .env.example -f compose.yaml up -d --no-deps api worker
```

确认依赖 healthy 后再迁移/启动应用，另一个窗口重跑 B1 的开发服务器命令；依赖已安装且未变可不重复 `npm ci`，也无需每次构建镜像。若源码/依赖变了，先构建再迁移；不要运行旧镜像配新源码。

**常见问题：** 重启后空账户先检查项目名/卷是否一致，不急着重建。再次启动仍会执行既有到期/删除策略，不是冻结数据；不要把重要资料放进临时验收项目后长期不管。

## Part C：真实 Qwen 接入准备（暂不执行，属于 S3-11）

### C1. 配置准备与秘密边界

**目的 / 预期：** 知道将来需要哪些配置，而不是现在调用模型。**操作位置：** 项目根目录；下面只检查示例/忽略规则，不读取真实文件。

```powershell
Set-Location -LiteralPath 'E:\GitHub\video-summary-app' -ErrorAction Stop
Test-Path -LiteralPath '.\.env'
git check-ignore .env
git ls-files -- .env
```

预期存在本地文件、被忽略、未跟踪。若已跟踪或配置位置不明，停止外部接入准备并反馈，不把内容贴出来。

根目录 [.env.example](../../../.env.example) 定义了：

| 项 | 准备用途与当前状态 |
| --- | --- |
| `DASHSCOPE_API_KEY` | 用户私下维护的真实密钥，仅后端/Worker 的安全注入；不进入前端、命令行参数、提示词、日志或 Git |
| `DASHSCOPE_BASE_URL` | 当前指定渠道为 `https://maas.qianwenaiapi.com/compatible-mode/v1`；S3-11 验证接入渠道，不自行换地址 |
| `QWEN_MODEL` | 当前选择 `qwen3.8-omni-flash`；选择已确定，可用性与适用输入仍待实际验证 |
| `APP_LOG_DIR` | 计划本机日志位置 `E:/video-summary-app-log`；当前 Compose/代码尚未把它作为日志挂载/输出配置 |

已有真实 `.env` 就保留，不用模板覆盖。后续由你用本地编辑器维护；不要放进 `frontend` 或加 `VITE_` 前缀暴露给浏览器。离线 Compose 当前并未加载这份真实配置，Key 存在不等于已经验证或授权计费。

### C2. 当前没有“修改开关即可上线 Qwen”的命令

**目的 / 预期：** 避免按不存在的切换流程误操作。**操作位置：** 根目录，可只读核对：

```powershell
Select-String -LiteralPath '.\backend\app\worker.py' -Pattern 'disabled','offline','MODEL_CALLS_ENABLED'
Select-String -LiteralPath '.\deploy\compose.yaml' -Pattern 'MODEL_CALLS_ENABLED','WORKER_PROCESSOR_MODE'
```

当前 Worker 只接受 `disabled` 和 `offline`；后者使用 `FakeModelAdapter`。Compose 将 `MODEL_CALLS_ENABLED` 固定为 false，Worker 也会拒绝开启外部调用。填 Key、改 BASE_URL、设置 `real`/`qwen` 都不会变成已接通的真实模式，可能反而无法启动。

真正切换必须等 S3-11：实现并审查供应商适配与配置注入、明确媒体输入路径、验证响应/用量/超时，然后发布匹配当时实现的启动步骤。本指南不编造可执行的真实调用命令，不提前安装 SDK、接入 API 或改业务逻辑。

### C3. 外发与计费测试准入清单

**目的 / 预期：** 为日后提出一个可审批的小批次。**操作位置：** 本地记录；以下不是现在的执行授权。

先核实接入账户/渠道、实际推理地区、适用留存/训练用途/删除政策和当前计费说明与你已审阅的条款一致；不重新讨论你是否同意账号条款，也不把“接受官方政策”改写成零留存或推理后立即删除。确认配额、超时、失败消耗 Token 时的收费和重试预算。

根据届时验证通过的输入路径列出实际外发内容：可能是完整视频/音频、JPEG 画面、派生音频、时间戳、提示与语言，或服务商可访问的限时媒体 URL。它不是“全流程本地”。不要发送非必需的用户身份、原始来源 URL 查询参数、本地路径、存储凭据。若需要模型回连，先验证受控 URL 的权限/过期/撤销，不公开本地文件夹或整个存储。当前没有已验收的公网回连方案。

准备以下批次说明，**明确批准后才能执行**；金额和调用数届时依据官方价格及估算填写，不能将此模板本身视作批准：

```text
批次：S3-11-B01（待批准）
目的：待选择，例如最小文本握手，或非敏感合成短视频接口验证
输入：明确类型、大小、时长、来源；是否外发视频/派生资料
模型/渠道/地域：明确实际配置与技术核验记录
请求数与自动重试上限：待填写；超出即停止
预估 Token 与费用：待填写计算依据和价格核验日期
硬预算与停止条件：待负责人批准；未知结果不盲目重发
失败费用：已消耗 Token 的失败可能收费，按实际渠道核验
真实用户/15 个评测素材：本批是否明确授权；默认不发送
保留证据：脱敏请求标识、版本、用量、错误、时长、JSON/账单核对
批准记录：批准人、时间、范围；未批准不执行
```

**成功判断：** 本阶段只能把这些内容记为准备/待验证，不产生模型请求或账单。本轮指南不向你索取密钥，也不自动发起审批或测试。真正接口、隐私路径、计费与 P90 验证留在 S3-11。

## 自动回归和这次人工验收的区别

| 方式 | 实际验证范围 | 不应据此声称 |
| --- | --- | --- |
| 后端 pytest，S3-10 记录 236 项通过 | 独立测试库/Schema、真实 FFmpeg、可控时钟/存储/模型替身、并发与故障注入 | 你已亲自在当前浏览器完成完整系统验收 |
| `npm run test:ui`，7 类检查 | 真正 React 构建 + 模拟 API 的 Chrome 交互，隔离浏览器会话 | 真实后端鉴权、对象存储、媒体解码全部由该脚本证明 |
| `local_baseline.py`，5/10/30 短时场景 | 临时 API、真实 PostgreSQL/私有 S3/Worker 的合成媒体闭环 | 家庭局域网持续负载、真实 Qwen、完整 2h/2GB 或正式 SLO 已通过 |
| 本指南 Part A/B，由你现场操作 | 当前 Windows 上真实页面连真实本地服务，观察账户、导入、配额、结果、管理与播放体验 | 离线替身能理解真实视频，或已完成 S3-11/S3-12 |

详细自动证据和重跑入口见 [REGRESSION_BASELINE.md](REGRESSION_BASELINE.md)；这次人工验收不要求顺便再跑全套回归，也不应把自动测试库误用作手动应用库。

## 已核实与尚待现场确认

本轮只读核实了 Compose 服务、卷/端口隔离、模式配置、迁移顺序、TTY 参数、Node 锁文件要求、路由及实际 UI；23 段 PowerShell 片段已仅解析语法、未执行，文档本地链接检查通过，S3-10 指纹中的 73 份源码/配置未改变；`video-summary-app-manual` 的 Compose 解析通过，未创建它的容器或卷。核查时没有运行容器占用 8000，也没有该验收项目的卷；这只是核查时状态，执行前仍须按 A3 再看。

此前已验证标准项目/隔离测试实例的构建、迁移、健康、启停、真实合成视频闭环；S3-10 的 236 项回归、7 类模拟 API 浏览器检查与短时容量记录没有在本次文档任务重复运行。本轮没有现场执行新的独立验收项目、Windows 交互式管理员创建、Docker 复制该 5 秒合成文件、浏览器实际播放跳转或你的完整人工清单。真实模型和历史副本处置同样未验证；本段是原指南核查记录，CR-001文档更新不新增业务验收证据。

已知现状：导入成功区有过时说明；Vite preview 不含开发代理；根目录 API/日志变量尚未接入真实模式；备份 PENDING 不应假装清除。它们均已在相应步骤说明，未通过改架构/业务逻辑掩盖。若发现其他启动问题，保留步骤号、命令退出码和相关错误，先反馈。

## 手动验收记录（完成后可填写）

运行日期：____　浏览器/版本：____　项目名：`video-summary-app-manual`　记录人：____

在“结果”列写 **通过 / 失败 / 未执行 / 未观察**，问题写步骤号、现象、相关错误和复现方法；不要附 `.env`、密码、Cookie、API Key 或私人视频。

| 检查项 | 结果 | 问题 / 备注 |
| --- | --- | --- |
| A1 项目目录正确，已有修改保留 | | |
| A2 Docker Linux Server/WSL2、Node/npm 可用 | | |
| A3 验收项目与旧卷隔离、8000/5173 空闲 | | |
| A4 PostgreSQL 和私有对象存储 healthy | | |
| A5 迁移成功，live/ready 均 200 | | |
| A6 管理员交互创建成功或现有凭据可用 | | |
| B1 真实网站可打开、代理连接正确 | | |
| B2 普通注册、登录、退出和再次登录 | | |
| B3 合成视频导入、排队不扣额 | | |
| 可选排队取消为 Cancelled、不扣额 | | |
| B4 Worker 正常，任务完成且仅扣一次 | | |
| 若自然发生 Failed，原因/退款/重试反馈 | | |
| B5 中英文页面、离线标识与 JSON 解析 | | |
| 原片播放及时间戳按钮体验 | | |
| B6 无效输入拒绝、不创建处理任务/扣额 | | |
| 管理员元数据默认可见，正文非默认可见 | | |
| 任务级用途/范围授权及撤销后的拒绝 | | |
| B7 可选测试任务删除，在线/备份状态区分 | | |
| B8 停止与重启后账户/数据仍在 | | |
| 全程未调用真实模型、未使用真实私人素材 | | |

完成后停止并反馈本表。**不要自动进入 S3-11 或 S3-12。**

## CR-001切换后的指南更新门槛

CR-WP06实施后用新的三范围/UUID请求编号/24小时/撤销/重新生成、管理员所有者直读及独立请求列表替换B6；以接受删除/内部验证完成和获批部署者诊断替换B7，不再引导用户查看删除历史或管理员人工页。WP07实际切换验证旧授权统一终止；新运行命令必须对应已交付代码并有证据，不在本轮编造。

S3-10原测试报告及JSON不覆盖。新手动结果单独记录版本/实例、通过/失败/未执行及限制，不附秘密、密钥或私人内容。完成后仍不能自动进入S3-11/12。
