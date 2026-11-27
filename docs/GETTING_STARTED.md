# 从这里开始

本指南从公开的 014 源码建立独立、可停启的本机实例。两条路径都能使用本地上传、原片播放、章节跳转和双语 Markdown。首次启动使用离线摘要替身，无需模型账号，结果正文为合成内容。

## 1. 下载和检查

下载 [Stage C 014 源码 ZIP](https://github.com/YatongXing/video-summary-app/releases/tag/v1.1.0) 并解压到新目录，或在 Git 中检出 `v1.1.0` 标签。在 PowerShell 中进入包含 README.md 的源码根目录。使用下面的新 Compose 项目 vsa-first014、空闲回环端口 58114，以及新私密文件和新的持久卷。[依赖及获取方式](DEPENDENCIES.md)

启动 Docker Desktop 并切换到 Linux 容器。两条路径都使用 Docker Desktop 和浏览器。路线 A 需要本机 Node.js 22.12 或更高版本及 npm。路线 B 用 Docker 运行 Node，无需在 Windows 安装 Node 或 Python。

```powershell
function Invoke-VsaDocker {
    & docker --context desktop-linux @args
    if ($LASTEXITCODE -ne 0) { throw "Docker failed with exit code $LASTEXITCODE" }
}

Invoke-VsaDocker info --format '{{.OSType}}'
Invoke-VsaDocker compose version
```

第一条应输出 linux。首次公开构建需要能访问 Docker Hub、npm、PyPI 和 Debian 包源。项目另有使用 Python 3.11 和 Chrome 或 Edge 的源码验证脚本，它创建临时项目进行回归测试。下文使用独立的持久化实例。[源码验证说明](../README.md#已有实例与开发验证)

## 2. 选择一条构建路线

### 路线 A · 用本机 Node 构建前端

```powershell
node --version
Push-Location .\frontend
try {
    npm.cmd ci --registry https://registry.npmjs.org --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
} finally {
    Pop-Location
}
```

看到构建成功并生成 `frontend/dist/index.html` 后，说明浏览器需要的静态页面已就绪。继续执行本节的「构建应用镜像」。

### 路线 B · 在 Docker 内构建前端

源文件只读挂载，npm 依赖安装在临时 Linux 容器中，只有静态产物写回 Windows。在还没有 frontend/dist 的新源码目录执行。

```powershell
$VsaFrontend = (Resolve-Path .\frontend).Path
$VsaDist = Join-Path $VsaFrontend 'dist'
if (Test-Path -LiteralPath $VsaDist) { throw 'Use a fresh source directory without frontend/dist' }
New-Item -ItemType Directory -Path $VsaDist -ErrorAction Stop | Out-Null
$VsaFrontendMounts = @(
    '--mount', "type=bind,source=$VsaFrontend,target=/input,readonly",
    '--mount', "type=bind,source=$VsaDist,target=/output"
)
$VsaBuildFrontend = 'mkdir /work; cd /work; cp /input/index.html /input/package.json /input/package-lock.json /input/tsconfig.json /input/vite.config.ts .; cp -R /input/src .; npm ci --registry https://registry.npmjs.org --no-audit --no-fund; npm run build; cp -R dist/. /output/'
Invoke-VsaDocker run --rm @VsaFrontendMounts node:22-bookworm-slim sh -ec $VsaBuildFrontend
```

### 构建应用镜像

从源码根目录执行。API 镜像包括 014 迁移和 FFmpeg，Worker 在该镜像基础上增加 OSS SDK，Web 镜像封装静态文件及同源 API 代理。两条公开路线使用相同的镜像配方。

```powershell
if (-not (Test-Path -LiteralPath .\frontend\dist\index.html)) { throw 'Frontend build output missing' }
$VsaApiImage = 'vsa-first014-api:local'
$VsaWorkerImage = 'vsa-first014-worker:local'
$VsaWebImage = 'vsa-first014-web:local'

Invoke-VsaDocker build --target runtime -t $VsaApiImage .\backend
Invoke-VsaDocker build -f .\deploy\Dockerfile.source-worker --build-arg "VSA_API_BASE=$VsaApiImage" -t $VsaWorkerImage .\deploy
Invoke-VsaDocker build -f .\deploy\Dockerfile.web -t $VsaWebImage .
Invoke-VsaDocker pull postgres:17-alpine
Invoke-VsaDocker pull rustfs/rustfs:1.0.4
```

<details>
<summary>已有维护者提供的预构建交付包？展开镜像加载步骤，可跳过本节的构建。</summary>


GitHub Release 提供公开源码 ZIP。项目维护者另行交付的 014 候选包包含 candidate/manifest.json、候选 Compose 配方和共享 dependencies/images.tar。归档包含候选 API、Worker、Web、Bilibili 下载器、PostgreSQL 和 RustFS，也包含回退及构建所需镜像。交付目录的 dependencies 另有构建用 wheel 和 npm 缓存。仅持有公开源码的用户可完整执行路线 B。

如果已有核验过的 014 包，可用下面的加载步骤替代本节的前端构建、镜像构建和拉取。把示例路径替换为收到的候选目录，保持 candidate 和 dependencies 的相对布局。使用第 3 节的新私密文件及公开源码的本地上传配方。归档中的 PostgreSQL 和 RustFS 标签可直接使用。

```powershell
$VsaPackage = 'C:\path\to\014-package\candidate'
$VsaManifest = Get-Content -LiteralPath (Join-Path $VsaPackage 'manifest.json') -Raw |
    ConvertFrom-Json
foreach ($Entry in $VsaManifest.files_sha256.PSObject.Properties) {
    $File = Join-Path $VsaPackage $Entry.Name
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ine $Entry.Value) {
        throw "Candidate package fingerprint mismatch: $($Entry.Name)"
    }
}
$VsaTar = (Resolve-Path -LiteralPath (Join-Path $VsaPackage $VsaManifest.archive)).Path
if ((Get-FileHash -LiteralPath $VsaTar -Algorithm SHA256).Hash -ine $VsaManifest.archive_sha256) {
    throw 'Image archive fingerprint mismatch'
}
Invoke-VsaDocker image load -i $VsaTar
$VsaImageEntries = @($VsaManifest.images.PSObject.Properties) + @($VsaManifest.infrastructure.PSObject.Properties)
foreach ($Entry in $VsaImageEntries) {
    $Actual = Invoke-VsaDocker image inspect --format '{{.Id}}' $Entry.Value.tag
    if ($Actual -ne $Entry.Value.id) { throw "Image ID mismatch: $($Entry.Name)" }
}
$VsaApiImage = $VsaManifest.images.api.tag
$VsaWorkerImage = $VsaManifest.images.worker.tag
$VsaWebImage = $VsaManifest.images.web.tag
```

</details>

## 3. 创建独立的本机实例

脚本把配置保存到当前 Windows 用户目录内、仓库外的 compose.env。它生成不同的初始化、维护、运行密码及两个本地 RustFS 凭据。十六进制字符可直接用于 PostgreSQL 连接串。维护身份只交给一次性维护服务。离线配置不会传入模型凭据。[部署参数模板](../deploy/wp07/.env.example)

新建前确认同名项目无容器、网络或卷，且所选端口空闲。需要避开同名项目或端口冲突时，修改脚本开头的值，并在以后停启时沿用。

```powershell
$VsaProject = 'vsa-first014'
$VsaPort = 58114
$VsaLabel = "label=com.docker.compose.project=$VsaProject"
if ((Invoke-VsaDocker ps -aq --filter $VsaLabel) -or
    (Invoke-VsaDocker network ls -q --filter $VsaLabel) -or
    (Invoke-VsaDocker volume ls -q --filter $VsaLabel)) {
    throw 'Project name is already in use'
}
if (Get-NetTCPConnection -State Listen -LocalPort $VsaPort -ErrorAction SilentlyContinue) {
    throw 'The loopback port is already in use'
}
$Conflicting = @(Get-ChildItem Env: | Where-Object { $_.Name -match '^(WP07_|VSA_S312_|SUPPORT_EMAIL$|BILIBILI_IMPORT_ENABLED$|COMPOSE_)' })
if ($Conflicting.Count) { throw 'Use a fresh PowerShell session without deploy variables' }
$env:COMPOSE_DISABLE_ENV_FILE = '1'

function New-VsaSecret {
    $Bytes = [byte[]]::new(16)
    $Rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Rng.GetBytes($Bytes)
        return -join ($Bytes | ForEach-Object { $_.ToString('x2') })
    } finally { $Rng.Dispose() }
}

$VsaPrivate = Join-Path $env:LOCALAPPDATA "VideoSummaryApp\$VsaProject"
$VsaEnvFile = Join-Path $VsaPrivate 'compose.env'
if (Test-Path -LiteralPath $VsaEnvFile) { throw 'A private settings file already exists' }
New-Item -ItemType Directory -Path $VsaPrivate -Force -ErrorAction Stop | Out-Null
@(
    "WP07_PROJECT_NAME=$VsaProject"
    "WP07_HTTP_PORT=$VsaPort"
    'WP07_DB=video_summary'
    "WP07_INIT_PASSWORD=$(New-VsaSecret)"
    "WP07_MAINT_PASSWORD=$(New-VsaSecret)"
    "WP07_RUNTIME_PASSWORD=$(New-VsaSecret)"
    "WP07_OBJECT_ACCESS_KEY=$((New-VsaSecret).Substring(0,20))"
    "WP07_OBJECT_SECRET_KEY=$(New-VsaSecret)"
    "WP07_APP_IMAGE=$VsaApiImage"
    "VSA_S312_WORKER_IMAGE=$VsaWorkerImage"
    "WP07_WEB_IMAGE=$VsaWebImage"
    'VSA_S312_MODE=offline'
    'VSA_S312_MODEL_CALLS=false'
    'VSA_S312_DEEPSEEK_ENABLED=false'
    'VSA_S312_SUPPORT_EMAIL='
    'BILIBILI_IMPORT_ENABLED=false'
) | Set-Content -LiteralPath $VsaEnvFile -Encoding ASCII -ErrorAction Stop

$VsaCompose = @('compose', '--project-name', $VsaProject, '--env-file', $VsaEnvFile,
    '-f', '.\deploy\compose.wp07.yaml', '-f', '.\deploy\compose.s3-12.yaml')
$VsaUrl = "http://127.0.0.1:$VsaPort"
Invoke-VsaDocker @VsaCompose config --quiet
```

使用 config --quiet 检查配置，避免把插值后的私密值显示在终端。Web 只绑定所选的 IPv4 回环端口，API、PostgreSQL 和 RustFS 只通过项目网桥访问。

### 初始化一次并开放

```powershell
Invoke-VsaDocker @VsaCompose up -d --wait --pull never --no-build db object-store
Invoke-VsaDocker @VsaCompose --profile initialize run --rm --no-deps --pull never volume-init
Invoke-VsaDocker @VsaCompose --profile initialize run --rm --no-deps --pull never initialize
Invoke-VsaDocker @VsaCompose up -d --no-deps --pull never --no-build api worker web

Invoke-VsaDocker @VsaCompose ps
curl.exe -sS -o NUL -w "%{http_code}$([char]10)" "$VsaUrl/api/health/live"
curl.exe -sS -o NUL -w "%{http_code}$([char]10)" "$VsaUrl/api/health/ready"
```

初始化应列出 001–014，并显示 CLOSED。Web 端口应显示 127.0.0.1:58114，API 启动后 live 返回 200，门禁关闭时 ready 返回 503。初始化失败或 live 未就绪时先排查原因，保留原卷和日志。

```powershell
Invoke-VsaDocker @VsaCompose --profile maintenance run --rm --no-deps --pull never maintenance python -m app.maintenance status
Invoke-VsaDocker @VsaCompose --profile maintenance run --rm --no-deps --pull never maintenance python -m app.maintenance open
curl.exe -fsS "$VsaUrl/api/health/ready"
```

等 ready 成功后刷新浏览器。

## 4. 注册账号并上传一个视频

1. 打开 [127.0.0.1:58114](http://127.0.0.1:58114/)，选择「注册账户」。填写邮箱和至少 8 字符的密码，随后用同一组信息登录。普通用户可自行注册，无需邮件验证或管理员账号。
2. 选择「本地文件」并上传短小、获准处理的 H.264 MP4。首次可使用合成样本。确认有上传及处理权后提交，文件通过校验后出现「导入成功」，随后在任务列表等状态变为「已完成」。
3. 打开结果，可播放原片并点击章节跳转。切换中文或 English，分别下载当前所选语言的 Markdown。离线结果会标明未分析真实语义。需要清退合成任务时用任务内的「删除任务」。

可用已加载或构建的 API 镜像生成三秒色块和音调。它写到新项目私有目录下单独的 samples 子目录。

```powershell
$VsaSample = Join-Path $VsaPrivate 'samples'
New-Item -ItemType Directory -Force -Path $VsaSample | Out-Null
$VsaSampleArgs = @(
    '--mount', "type=bind,source=$VsaSample,target=/out", $VsaApiImage,
    '-n', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=15:d=3',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '/out/first-014.mp4'
)
Invoke-VsaDocker run --rm --network none --user 0:0 --entrypoint ffmpeg @VsaSampleArgs
```

### 可选能力

本指南的本地上传配置未启用 Bilibili。该功能另有 compose.stage-c.yaml、受限出站服务和下载器镜像。维护者交付包包含下载器，也可从公开配方单独构建。先按部署指南设置 VSA_BILIBILI_IMAGE、核对套接字卷与出口，再通过 BILIBILI_IMPORT_ENABLED 启用新导入。视觉路径还要求 Worker 可读取 DejaVu Sans 字体。获准且可匿名获取的 BV 页面可以导入，结果提供纯文本章节时间戳和双语 Markdown。[下载器构建和隔离验证](../deploy/README.md#stage-c-第二阶段发布演练)

需要真实摘要时，先等当前任务结束。使用 `notepad.exe $VsaEnvFile` 打开仓库外的 `compose.env`，替换原有模式和调用开关，补充 Worker 使用的千问密钥。建议把普通 Qwen 的等待上限设为 600 秒。密钥值使用自己的授权凭据。真实模型可能计费。

```dotenv
VSA_S312_MODE=qwen
VSA_S312_MODEL_CALLS=true
VSA_S312_KEY=replace_with_your_authorized_key
VSA_S312_TIMEOUT=600
```

要在下一次导入时选择 DeepSeek，将 `VSA_S312_DEEPSEEK_ENABLED` 设为 `true`。如果需要 Qwen 大视频转存，在同一个文件设置 `VSA_S312_OSS_REGION`、`VSA_S312_OSS_BUCKET`、`VSA_S312_OSS_ACCESS_KEY_ID` 和 `VSA_S312_OSS_ACCESS_KEY_SECRET`，使用独立私有 OSS 桶及最小权限。 `WP07_OBJECT_*` 仍是本地 RustFS 凭据。[千问与 OSS 配置](guides/QWEN_OSS.md)

保存后重新加载 API 和 Worker，确认就绪，再新建任务验证真实模式。恢复离线时将模式设为 `offline`，两项模型开关设为 `false`，移除模型与 OSS 密钥后执行同一组命令。

```powershell
Invoke-VsaDocker @VsaCompose config --quiet
Invoke-VsaDocker @VsaCompose up -d --no-deps --pull never --no-build api worker
curl.exe -fsS "$VsaUrl/api/health/ready"
```

## 5. 停止、重启与排查

等任务处理和已请求的删除结束后，通过维护命令关门并停止。stop 保留容器定义和命名卷。下次开启 PowerShell 后，从源码根目录重新定义 Invoke-VsaDocker，再使用原项目名、端口与 env 文件设置以下变量。已有实例无需再次执行 initialize。

```powershell
$VsaProject = 'vsa-first014'
$VsaPort = 58114
$VsaEnvFile = Join-Path $env:LOCALAPPDATA "VideoSummaryApp\$VsaProject\compose.env"
$env:COMPOSE_DISABLE_ENV_FILE = '1'
$VsaCompose = @('compose', '--project-name', $VsaProject, '--env-file', $VsaEnvFile,
    '-f', '.\deploy\compose.wp07.yaml', '-f', '.\deploy\compose.s3-12.yaml')
$VsaUrl = "http://127.0.0.1:$VsaPort"

# 停止
Invoke-VsaDocker @VsaCompose --profile maintenance run --rm --no-deps --pull never maintenance python -m app.maintenance close
Invoke-VsaDocker @VsaCompose stop

# 下次启动
Invoke-VsaDocker @VsaCompose up -d --wait --pull never --no-build db object-store
Invoke-VsaDocker @VsaCompose up -d --no-deps --pull never --no-build api worker web
Invoke-VsaDocker @VsaCompose --profile maintenance run --rm --no-deps --pull never maintenance python -m app.maintenance open
curl.exe -fsS "$VsaUrl/api/health/ready"
```

保留命名卷和私密文件。修改模型配置后，使用同一 Compose 参数运行 up -d --no-deps --pull never --no-build api worker 重新加载配置。restart 继续使用原容器已有配置。

| 现象 | 先检查 |
| --- | --- |
| Docker 或绑定挂载出错 | 确认 Docker Desktop 已启动、上下文为 Linux，且 Docker Desktop 能访问 Windows 源码和产物目录。 |
| 打不开网页 | 通过本项目的 compose ps 核对 Web 只绑定所选回环端口，再查 web 和 api 日志。 |
| live 为 200、ready 为 503 | 用维护通道查 status。首次是预期的 CLOSED，核对初始化成功后运行 open。 |
| 数据库或对象存储不健康 | 查本项目的 db 和 object-store 日志及依赖的配置。保留私密文件和持久卷。 |
| 任务持续排队 | 确认 ready 正常及 Worker 已启动。离线模式使用 offline、false，DeepSeek 开关保持 false。 |
| 媒体校验失败 | 使用受支持的 H.264 MP4 或本指南生成的合成样本。 |

使用下面的命令查看本项目近期日志。公开求助时只分享脱敏错误、系统与 Docker 版本、源码标签和退出状态。[详细部署与恢复](../deploy/README.md) · [验收证据与测试范围](E2E_TEST_PLAN.md)

```powershell
Invoke-VsaDocker @VsaCompose logs --tail 80 web api worker
```
