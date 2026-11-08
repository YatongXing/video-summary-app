# WP07-A 正式部署移交

2026-11-08，关卡 A、B、C1 和 C2 均通过。正式项目 **vsa-local** 保持 **OPEN**，Web 入口为 [127.0.0.1:58080](http://127.0.0.1:58080)。API、Worker、PostgreSQL、RustFS 均无宿主端口。真实 Qwen 关闭，应用使用离线 FakeModel。

## 部署基线

| 项目 | 已验收值 |
| --- | --- |
| Git 基线 | `main` / `6aed230f3095e671d1b7a6330eaac994068c384f` |
| 冻结输入 | 61 项逐文件匹配原 SHA-256，含 001～011、10 个前端源码和 3 个前端产物。正式库迁移表、运行镜像和原冻结值逐项一致。 |
| Compose SHA-256 | `e44683998b7421e8def837d3de79bcc9ec0c9ed090ca5c119a126bf652965e6e` |
| 数据和网络 | PostgreSQL `video_summary`、RustFS 私有桶 `video-summary-private`、Docker 网络 `vsa-local_private` |
| 命名卷 | `vsa-local_postgres-data`、`vsa-local_object-data`、`vsa-local_deletion-control`、`vsa-local_input-staging`、`vsa-local_processing-work` |
| 运行身份 | Web、API、Worker 以 UID `10001` 运行。数据库初始身份为 `vsa_init`，数据库、schema 和表由 `vsa_maint` 持有，应用使用 `vsa_runtime`。 |
| 凭据 | 部署者在仓库外保管独立数据库和 RustFS 凭据。文件 `%LOCALAPPDATA%\VideoSummaryApp-vsa-local\compose.env` 的权限仅授予当前 Windows 用户。 |

| 容器 | 已验证本地镜像标签 | 实际镜像 ID |
| --- | --- | --- |
| `vsa-local-api-1`、`vsa-local-worker-1` | `video-summary-app-wp07a-candidate:6aed230-8990a993` | `sha256:8e62c096147210e11d14bd9d24c08eb72995cbf1dc82b5e1013ffae895f9698e` |
| `vsa-local-web-1` | `video-summary-app-wp07a-web-candidate:6aed230-8990a993` | `sha256:5c68999a3cd9b9f3cc7408c0e5bc7faaee2c90cf9a75bb76933f5e2a745ff097` |
| `vsa-local-db-1` | `postgres:17-alpine` | `sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b` |
| `vsa-local-object-store-1` | `rustfs/rustfs:1.0.4` | `sha256:ed9210d6411d77ffceb70d94b964a3cb7d225983368bc25bebe01e995293dcc2` |

## 日常维护

使用 Docker Desktop 的 `desktop-linux` 上下文。下列路径已对现有五个容器实测，Compose 配方保留批准字节。现有容器的 Compose 标签保留创建时的 C 盘路径供追溯。运维一律显式指定 E 盘配方。

```powershell
$Wp07Recipe = 'E:\VideoSummaryApp-Records\WP07-A\deployment\deploy\compose.wp07.yaml'
$Wp07EnvFile = Join-Path $env:LOCALAPPDATA 'VideoSummaryApp-vsa-local\compose.env'
$Wp07ComposeArgs = @('--context','desktop-linux','compose','--project-name','vsa-local','--env-file',$Wp07EnvFile,'-f',$Wp07Recipe')
& docker @Wp07ComposeArgs config --quiet
& docker @Wp07ComposeArgs ps
& docker @Wp07ComposeArgs --profile maintenance run --rm --no-deps --pull never maintenance python -m app.maintenance status
```

依 [正式 Runbook](CR-WP07A_FORMAL_RUNBOOK.md) 通过维护身份执行 `close`。故障时收口入口和排空服务须按 Runbook 确认目标。恢复后核对控制身份、清理进度及巡检，再按授权执行 `open`。运维启动使用已核验的镜像 ID、`--no-build` 和 `--pull never`。E 盘明文配方用于现有实例运维，完整冻结构建上下文在加密证据中。

## 巡检与通知

计划任务 **Video Summary App WP07-A Monitor** 由当前 Windows 用户登录时以普通权限启动。轮询间隔 30 秒，持续故障阈值 60 秒。巡检以 `%USERPROFILE%\VideoSummaryApp-WP07A-Monitor` 下的冻结脚本和正式状态文件运行。Windows Application 事件源为 **Video Summary App WP07-A**，桌面通知发往同一登录会话。事件 701、702、703 分别为故障、持续故障、恢复。正常 CLOSED 观测无故障且业务未就绪。

## 验收及清理

- A 复验了冻结镜像、迁移、CLOSED 门禁、权限和合成业务。B 用已验证镜像初始化正式实例。C1 验证了实际本机绑定和 CLOSED 下的 HTTP 行为。
- C2 核验了两种合成角色的上传、处理、中英文摘要与导出、视频播放跳播、跨角色隔离及删除。两项清理均为 COMPLETED。RustFS、暂存和处理目录为空。两个合成账号已禁用，四个会话已撤销。数据库保留按应用规则留存的审计及删除控制记录。
- C2 巡检测试收到三条 Windows 事件与桌面显示回执，持续故障间隔 64.94 秒。最终正式观测 OPEN、无故障、业务已就绪。

## 证据与归档

原件位于 `E:\VideoSummaryApp-Records\WP07-A\WP07-A-evidence.zip.dpapi`。E 盘为 exFAT，证据包以 Windows 当前用户 DPAPI 加密。`evidence-manifest.json` 记录四个目录内 143 个文件的大小和 SHA-256。可使用同一 Windows 用户运行 `Restore-WP07ARecords.ps1` 校验或解包到新的 NTFS 私有目录。凭据文件和运行数据卷继续由现有位置保管。

项目内保留 [正式 Runbook](CR-WP07A_FORMAL_RUNBOOK.md)、[部署方案](CR-WP07A_LOCAL_DEPLOYMENT_PLAN.md)、[安全复核](CR-WP07A_SECURITY_REVIEW.md)、[测试规范](CR-WP07A_TEST_SPEC.md) 及 [冻结指纹](baseline/cr-wp07a-fingerprints.json)。失效的实施审批契约见 [归档前版本](https://github.com/YatongXing/video-summary-app/blob/6aed230f3095e671d1b7a6330eaac994068c384f/docs/CR-WP07A_IMPLEMENTATION_CONTRACT.md)。阶段原始报告继续作为隔离验证证据。

## 后续工作

- 明确长期 ADMIN 的创建方式、凭据保管和 Windows 巡检响应责任。桌面通知依赖当前用户登录。
- 保留已验证镜像和正式持久卷，按应用规则管理删除控制与审计。未来更换镜像时重新核对冻结输入和实际镜像 ID。
- S3-11 真实 Qwen 接入、超出本机的访问与新增备份按独立审批处理。
