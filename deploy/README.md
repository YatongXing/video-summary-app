# S3-01 本地运行

从 `deploy/` 目录运行。需要 Docker Desktop（Compose v2）；当前配置将数据库和对象存储留在私有容器网络，仅把 API 绑定到本机 `127.0.0.1:8000`。`deploy/.env.example` 只含开发用占位凭据，不含 Qwen Key；Compose 不引用仓库根目录 `.env`，模型调用保持关闭。正式使用或允许局域网访问前必须替换开发凭据并重新审查网络边界。

```powershell
cd deploy
docker compose --env-file .env.example up --build -d
docker compose --env-file .env.example ps
Invoke-WebRequest http://127.0.0.1:8000/health/live
Invoke-WebRequest http://127.0.0.1:8000/health/ready
docker compose --env-file .env.example down
```

前端开发服务器另起终端运行，默认只监听本机 `127.0.0.1:5173`，并把 `/api/health/*` 转发到本机 API：

```powershell
cd frontend
npm install
npm run build
npm run dev
```

页面地址为 `http://127.0.0.1:5173/`。当前仅展示 API 存活状态；账户、视频任务和真实模型调用均属于后续工作包。停止前端时在其终端按 Ctrl+C；停止容器使用上面的 `docker compose ... down`，不加 `--volumes`，以保留本地测试数据。若构建了 `package-lock.json`，后续安装改用 `npm ci`。

对象存储首轮选用单实例 RustFS，使用其 S3 兼容 API；镜像固定为 `rustfs/rustfs:1.0.4`，健康检查使用官方说明中的 `/health` 端点。原先引用的 MinIO 镜像在本地首次拉取时返回 401，因此改用可拉取的 [RustFS 官方镜像](https://github.com/rustfs/docs.rustfs.com/blob/main/content/en/installation/container/docker.md)。Compose 的健康依赖遵循 [Docker 官方文档](https://docs.docker.com/compose/how-tos/startup-order/)。这只是 S3-01 本地运行选择，后续仍需验证 S3 操作兼容性、备份和删除要求。

## S3-02 数据库迁移

首次启动使用真实业务数据前，先从 `deploy/` 明确执行迁移；API 和 Worker 不会在启动时各自抢跑迁移。以下示例只使用 `.env.example` 的本地占位凭据，不读取仓库根目录的真实 `.env`：

```powershell
docker compose --env-file .env.example up -d db
docker compose --env-file .env.example build api
docker compose --env-file .env.example run --rm --no-deps api python -m app.migrate
```

重复执行迁移会核对已应用文件的校验值并报告 `Applied: none`。集成测试须显式设置指向独立、以 `_test` 结尾的数据库的 `TEST_DATABASE_URL`；`compose.test.yaml` 仅用于测试，在本机回环地址开放 PostgreSQL 的 `15432` 端口。测试完成后用 `docker compose -f compose.yaml -f compose.test.yaml --env-file .env.example down` 停止容器，不加 `--volumes`。


## S3-03 账户与访问控制

先按上节执行迁移（新增 `002_auth.sql`），再启动 API。前端运行 `npm run dev` 后可在 `http://127.0.0.1:5173` 注册、登录、查看当前账户和退出。注册不自动登录；注册参数不能指定角色。账户持久保存在 PostgreSQL，密码采用独立随机盐的 scrypt（N=32768、r=8、p=3），参考 [OWASP 密码存储指引](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)。密码首轮接受 8–128 个字符，登录不回显密码验证错误内容。

会话是数据库中的可撤销记录，浏览器只持有随机 HttpOnly、SameSite=Strict Cookie；数据库仅存令牌哈希。首轮服务端有效期为 8 小时（`AUTH_SESSION_SECONDS=28800`），不设置持久 Cookie 或免密码登录；浏览器恢复会话可能保留会话 Cookie，因此应使用“退出登录”明确撤销。认证后变更请求须携带 `/auth/login` 或 `/auth/me` 返回的 `csrf_token`，通过 `X-CSRF-Token` 请求头提交。前端只在内存保存该值，不使用 localStorage。

本地 HTTP 回环环境默认 `AUTH_COOKIE_SECURE=false`；允许局域网访问前须在部署验收中配置 HTTPS、设置为 `true` 并核对 `AUTH_ALLOWED_ORIGINS`，不能把密码通过不受控网络明文传输。API 明确禁用 Uvicorn 代理头信任，来源限速不使用用户可伪造的 `X-Forwarded-For`。Vite 开发代理后的访问会被视为同一来源，源级限速是共享的；账号级限速仍独立生效。

已批准登录限速为每账号 15 分钟内 5 次失败、每来源 15 分钟内 20 次失败，各暂停 15 分钟；注册首轮默认每来源 15 分钟 20 次请求，达到后暂停 15 分钟。示例配置提供阈值、窗口及暂停秒数，可在本机调整。限速状态持久化于 PostgreSQL，重启 API 不会清零。邮箱验证和密码找回按 MVP 决策暂未实现。

部署者在交互终端使用下列入口创建管理员，密码由隐藏提示读取，不接受命令行密码参数；同名账户不会被自动提升权限。禁用账户会撤销其现有会话。最终交付时再创建实际管理员账户。

```powershell
docker compose --env-file .env.example run --rm --no-deps api python -m app.admin create-admin --email admin@example.com
docker compose --env-file .env.example run --rm --no-deps api python -m app.admin disable-user --email admin@example.com
```

认证接口为 `/auth/register`、`/auth/login`、`/auth/me`、`/auth/logout`。`GET /tasks/{id}` 仅提供任务所有者的必要元数据；`GET /admin/tasks` 仅向管理员提供分页元数据。所有者可通过 `POST /tasks/{id}/consents` 授予指定 `purpose` 和 `access_scope`（`video` 或 `summary`）的授权，使用 `DELETE /tasks/{id}/consents/{consent_id}` 撤销。复用的内容授权检查每次核实任务归属、删除栅栏和当前同意，并记录管理员允许或拒绝访问的审计。本工作包尚无视频/摘要内容传输接口，进行中流式传输的中断验证留在实际内容接口实现时完成。

S3-03 自动化测试仅使用显式指定且以 `_test` 结尾的 PostgreSQL 测试库，不读取真实 `.env`，不调用外部模型。运行方式为在后端环境设置 `TEST_DATABASE_URL` 后执行 `python -m pytest -q`。尚未实现的上传、额度、队列处理和删除执行按实施计划后续工作包推进。


## S3-04 私有媒体导入

产品负责人于 2026-10-29 批准首轮输入收敛为 **MP4/MOV**，视频仅 H.264/AVC，音轨若存在仅 AAC/MP3；WebM 不在 MVP 范围。原因依据 [WebM 官方容器说明](https://www.webmproject.org/docs/container/)：标准 WebM 的 VP8/VP9、Vorbis/Opus 与此前编码白名单冲突。本轮没有扩充编码支持。2 GB 按 2,000,000,000 字节、2 小时按 7200 秒校验，边界值允许，超出则拒绝。

先构建新版 API/Worker 并执行迁移，新增 `003_inputs.sql` 保存临时导入登记及音轨标记。登录后可在页面选择本地文件或 HTTPS 直达链接，并逐视频确认处理授权。`POST /inputs/upload` 接收原始文件字节（非 multipart），通过 `X-Video-Filename` 传入百分号编码的展示文件名、`X-Upload-Authorized: true` 确认授权；`POST /inputs/url` 接收 `{url, rights_confirmed: true}`。两者均需会话和 `X-CSRF-Token`。文件名只用于展示，不能决定存储路径。URL 的查询参数、凭据和原始地址不落入任务、导出或错误信息。

API 先流式暂存至私有 `input-staging` 卷，检查实际容器、全部音视频编码、时长及完整解码，随后写入私有 RustFS bucket 并在事务中登记原片资产和 Queued 任务。只有校验成功才记录排队提交时间；输入失败或客户端中断不创建处理任务，不扣处理机会。成功只表示导入完成，Worker 尚未绑定真实处理器，不会开始模型处理。

HTTPS 下载仅使用经校验的公网 IP，TLS 证书及 SNI 仍验证原域名；不使用环境代理，不发送用户 Cookie/认证头。首轮只接受标准 443 端口，拒绝链接内用户名密码、内网/回环/保留及过渡地址；最多 3 次跳转且每次重新验证。实际字节数和响应长度均检查，拒绝页面、播放清单、压缩响应及不完整下载。[FFprobe/FFmpeg 官方说明](https://ffmpeg.org/ffprobe-all.html)是媒体探测及协议白名单的依据；本地媒体探测仅允许 file/pipe 协议和 mov 解复用器，不调用网络媒体来源。

首轮实现默认最多 5 个并行导入；上传/下载各默认 1800 秒，探测 30 秒，完整解码 3600 秒。示例配置可调整：上传/下载最大 3600 秒、探测最大 120 秒、解码最大 10800 秒，对象写入另有 1800 秒保护。这些是实现资源保护，不是已验证的容量或 P90 承诺；超时不会静默截断或以成功入队。完整解码在 API 的后台线程执行，视频时长越长，校验等待也可能越长。

导入登记与资产写入支持失败清理；清理失败保留登记供重试，清理与最终入队持有相同登记行锁。活动校验/存储期间续期；失联登记默认 6 小时失效，API 启动后及每 60 秒检查并清理过期暂存/孤立对象。此暂存清理不替代后续 S3-09 的任务删除、告警和 30/90 天保留流程。前端“中止导入”只中断当前请求；若成功提交与中止同时发生，不能据此宣称已取消排队任务。

API 使用 `S3_ENDPOINT_URL`、`S3_ACCESS_KEY`、`S3_SECRET_KEY` 和 `S3_BUCKET` 访问本机私有存储，Compose 只从无模型密钥的环境示例注入开发占位值。对象存储未开放主机端口、未生成公开或签名媒体 URL，也没有调用 Qwen。

真实媒体测试可用专用镜像运行：`docker build --target test -t video-summary-app-test -f backend/Dockerfile backend`（从仓库根目录）。测试镜像单独包含 pytest/httpx 和测试文件，Compose 运行目标固定为 runtime；集成测试仍须显式指定独立 `_test` 数据库。实际 2 GB/2 小时大文件和本机并发容量未在本轮测量，自动化测试使用短合成视频及受控边界输入。


## S3-05 任务、额度与重试

先构建新版容器并执行迁移，新增 `004_task_rules.sql` 的模型超时重试计数。登录后页面显示普通用户额度、恢复倒计时，以及分页任务列表；导入成功、取消或重试后刷新，显示中的倒计时不决定实际操作资格。管理员显示不限处理机会。`GET /quota` 和 `GET /tasks` 返回当前用户数据；`POST /tasks/{id}/cancel`、`POST /tasks/{id}/retry` 需有效会话和 CSRF。重试 JSON 为 `{request_key: UUID字符串}`，请求失败结果未知时须复用相同标识，不能重新生成来重复接受同一意图。

普通用户初始 3 次，仅首次实际开始扣一次。同任务后续重试即使已获退款也不再次扣额；管理员不产生扣额流水。余额低于 3 后按每 6 小时恢复一次计算，后续使用不重置计时，达到 3 时停止；退款不重置仍在进行的恢复计时。服务端在读取额度或相关事务时补算离线/未登录期间已到期的恢复，不依赖浏览器运行计时器。余额、事件流水与任务变更在同一 PostgreSQL 事务中完成，按用户再按任务加锁，并依靠任务级扣额/退款唯一键防止重复。

内部、外部超时/限流/不可用及未知失败按既定规则返还已扣且未返还的机会，余额封顶 3；明确用户输入问题和处理中的主动取消不退款。排队取消不扣额，取消和成功为终态；迟到失败、成功或取消不能覆盖已经提交的终态。并发取消/失败以权威事务先提交的最终分类为准，原因和结果留在任务/尝试及非正文审计记录。

手动和系统调用共用重试规则与计数。滚动窗口采用 `(当前时间－5分钟, 当前时间]`，恰好 5 分钟前的记录退出窗口。前 4 次已接受重试不触发冷却，第 5 次已接受重试触发，冷却阶梯依次为 1/5/15/30 分钟并封顶；冷却期间请求不增加计数、不延长等待。累计接受 20 次后拒绝第 21 次。第 5 次触发冷却后任务仍为 Queued，但内部开始入口必须等冷却结束。取消或成功后不能接受新重试；已接受请求的重复投递只返回当前结果，不重复计数。第 4 次及之后的模型超时重试显示可能产生 API 费用的提示，处理机会不因此再次扣除。

`app.tasks.start_task/fail_task/succeed_task` 是供后续 Worker 使用的内部事务入口，不开放为浏览器 API。开始与终结记录处理尝试和代次；终结写入检查当前代次、活动状态和删除栅栏。成功入口的可信调用方仍须先完成真实输出验证。队列与租约能力见 S3-06；运行时尚未绑定真实处理器，未启用实际处理、自动重试循环或模型调用；处理中取消对实际媒体/模型工作的中断在对应 Worker 工作包接入。完整回归使用假时钟、独立 PostgreSQL schema 和并发线程，不调用真实模型。


## S3-06 持久队列与 Worker 租约

构建新版镜像并执行迁移 `005_queue.sql`，新增 `schedule_events` 调度元数据：原始提交时间、用户/任务排序键、领取 Worker、代次、启动及受阻/越过/恢复事件和关联任务。该记录跟随源任务的 30 天元数据期限，由 S3-09 接入清理；不记录视频、摘要或 URL 正文。用户任务状态仍只有已有五种。

调度按 `(submitted_at, user_id, task_id)` 选择，数据库单一调度临界区保证两个 Worker 不同时突破总并发或重复领取。普通用户最多一个活动任务；管理员受全局并发和 FIFO 约束。只有前方任务因普通用户已有活动任务、且除此之外符合条件时，才允许其他用户任务越过；同用户后续任务不会抢先启动。额度不足、冷却/等待、账户停用等其他原因会保守地阻塞队列，不扩展为一般优先级调度。调度不会使用 `SKIP LOCKED` 偷换顺序。

公平性采用已有排序而非新增年龄优先级：用户活动任务释放后，其最早符合条件任务仍排在更晚提交者之前；记录实际越过和阻塞原因以便检查。此行为已做功能测试，不代表已测出长期饥饿的时间上界或 10–30 人的容量。不可运行的队首需要按原有业务规则解除阻塞，不能用所谓防饥饿策略绕过已批准限制。

首轮全局并发默认 `QUEUE_GLOBAL_CONCURRENCY=1`，可按实际本机测量调整；这不是容量承诺。默认租约 `WORKER_LEASE_SECONDS=120`、心跳 `WORKER_HEARTBEAT_SECONDS=30`，必须为正且心跳小于租约；轮询 `WORKER_POLL_SECONDS=1`。API 和 Worker 注入一致配置。调度领取、首次扣额、活动状态、租约和处理尝试在同一事务提交。

心跳和数据库写入须核对当前 Worker、代次、未过期租约、Processing 状态、账户有效性及未删除标记。`queue.fenced_write` 在同一数据库事务中保护资产登记等写入；外部对象仍须先私有暂存，在登记被拒时由后续处理流程清理，不能把数据库事务误称为跨存储原子提交。过期或缺失租约的活动任务由恢复事务记为内部失败并幂等退款、提高代次，已请求删除的任务按取消规则释放活动名额。旧 Worker 恢复后不能续约或写入。

恢复不会自动重新调用模型：外部请求是否完成可能未知，任务须经过 S3-05 的重试入口重新排队。相同任务不再次扣额，手动和系统重试共用冷却与累计限制。取消或账户停用后，Worker 的下一次检查会向处理器发出停止信号；即使处理器不配合，租约及终态检查仍阻止其继续提交数据库结果。

`worker.run(..., processor=callback)` 是后续 S3-07 和测试使用的内部接入点。处理器通过 `WorkerContext` 检查取消、执行受租约保护的写入，并在实际输出验证完成后显式确认成功；仅返回函数不会被视为总结成功。**当前命令行 Worker 未绑定真实处理器，因此不会领取排队视频或扣额**，但会执行过期租约恢复。没有公开的开始/成功/失败接口，也没有运行时假总结模式。

任务数据库连接/SQL/锁等待默认受 `TASK_DB_TIMEOUT_SECONDS=5` 保护，不覆盖连接字符串中的测试 schema 配置。`WORKER_SHUTDOWN_SECONDS=5` 为观察到停止后的结算和回调等待预算；已发出的数据库操作可能另外等待自身超时。停止不再领取任务，无法及时结算时交由租约过期恢复。不承诺强制终止任意 Python 线程；不合作的处理器不能占用进程退出，且不能带着旧令牌提交结果。

独立测试使用隔离 PostgreSQL schema、可控时钟、合成处理器及并发 Worker；另有实际子进程被强制结束后的恢复验证。以上验证不包含模型请求、真实视频处理、完整备份恢复或本机负载容量测量。
