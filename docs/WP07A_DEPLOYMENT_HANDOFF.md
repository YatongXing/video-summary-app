# WP07-A 正式部署移交

本页保留 2026-11-08 的 WP07-A 首次部署、离线验收和历史证据。当前正式实例运行 S3-12，更新入口见[本机运维](../deploy/README.md)。

## 部署基线

| 项目 | 已验收值 |
| --- | --- |
| Git 基线 | `main` / `6aed230f3095e671d1b7a6330eaac994068c384f` |
| 冻结输入 | 61 项逐文件匹配原 SHA-256，含 001～011、10 个前端源码和 3 个前端产物。正式库迁移表、运行镜像和原冻结值逐项一致。 |
| Compose SHA-256 | `e44683998b7421e8def837d3de79bcc9ec0c9ed090ca5c119a126bf652965e6e` |
| 数据和网络 | PostgreSQL `video_summary`、RustFS 私有桶 `video-summary-private`、Docker 网络 `vsa-local_private` |
| 命名卷 | `vsa-local_postgres-data`、`vsa-local_object-data`、`vsa-local_deletion-control`、`vsa-local_input-staging`、`vsa-local_processing-work` |
| 运行身份 | Web、API、Worker 以 UID `10001` 运行。数据库初始身份为 `vsa_init`，数据库、schema 和表由 `vsa_maint` 持有，应用使用 `vsa_runtime`。 |
| 凭据 | 部署者在仓库外保管独立数据库和 RustFS 凭据。私密 `compose.env` 的文件权限仅授予部署者。 |

| 容器 | 已验证本地镜像标签 | 实际镜像 ID |
| --- | --- | --- |
| `vsa-local-api-1`、`vsa-local-worker-1` | `video-summary-app-wp07a-candidate:6aed230-8990a993` | `sha256:8e62c096147210e11d14bd9d24c08eb72995cbf1dc82b5e1013ffae895f9698e` |
| `vsa-local-web-1` | `video-summary-app-wp07a-web-candidate:6aed230-8990a993` | `sha256:5c68999a3cd9b9f3cc7408c0e5bc7faaee2c90cf9a75bb76933f5e2a745ff097` |
| `vsa-local-db-1` | `postgres:17-alpine` | `sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b` |
| `vsa-local-object-store-1` | `rustfs/rustfs:1.0.4` | `sha256:ed9210d6411d77ffceb70d94b964a3cb7d225983368bc25bebe01e995293dcc2` |

## 日常维护

当前更新命令、配方和状态检查见 [S3-12 本机运维](../deploy/README.md)。WP07-A 当时使用独立的 E 盘 Compose 副本，基础配方 SHA-256 为 `e44683998b7421e8def837d3de79bcc9ec0c9ed090ca5c119a126bf652965e6e`。同一配方现由仓库和 S3-12 交付包保留，现行更新由正式 API 容器的 Compose 标签定位既有私密配置。


## 巡检与通知

**现行状态（2026-11-08）**。负责人要求停用 Windows 定时巡检和桌面通知。复核计划任务为 `Disabled`，对应进程为 0。以下为 WP07-A 验收时的记录。

计划任务 **Video Summary App WP07-A Monitor** 由当前 Windows 用户登录时以普通权限启动。轮询间隔 30 秒，持续故障阈值 60 秒。巡检以 `%USERPROFILE%\VideoSummaryApp-WP07A-Monitor` 下的冻结脚本和正式状态文件运行。Windows Application 事件源为 **Video Summary App WP07-A**，桌面通知发往同一登录会话。事件 701、702、703 分别为故障、持续故障、恢复。正常 CLOSED 观测无故障且业务未就绪。

## 验收及清理

- A 复验了冻结镜像、迁移、CLOSED 门禁、权限和合成业务。B 用已验证镜像初始化正式实例。C1 验证了实际本机绑定和 CLOSED 下的 HTTP 行为。
- C2 核验了两种合成角色的上传、处理、中英文摘要与导出、视频播放跳播、跨角色隔离及删除。两项清理均为 COMPLETED。RustFS、暂存和处理目录为空。两个合成账号已禁用，四个会话已撤销。数据库保留按应用规则留存的审计及删除控制记录。
- C2 巡检测试收到三条 Windows 事件与桌面显示回执，持续故障间隔 64.94 秒。最终正式观测 OPEN、无故障、业务已就绪。

## 证据与归档

2026-11-08 的移交记录确认，C 盘关卡 A、B、C1、C2 四个源目录在加密归档逐文件校验后已清理。原始材料共 143 个文件、1,484,306 字节，分别为 A 133 个、B 2 个、C1 2 个、C2 6 个。归档使用 Windows 当前用户 DPAPI，加密包 SHA-256 为 `7a9ec82b9e4f75445fe5e4cd5ea60e036b8671a8e71970ad1f00843e86bfea71`。历史复核记录中，五个正式凭据的精确匹配为零；私密环境文件与正式数据卷留在各自位置。

移交后，旧 Compose 曾成功识别五个正式容器，就绪接口返回 200，C2 的故障、持续故障和恢复通知均有验收记录。正式实例后来升级到 S3-12，Windows 定时通知已由负责人停用。2026-11-09 按负责人批准，外部 WP07-A 的六个原始证据文件、重复 Compose 配方和旧 S3-11 设置索引已移除。两份保留的 Compose 副本与原指纹一致，现行更新预演和就绪检查通过。本节保留历史验收与校验摘要。


项目内保留 [正式 Runbook](CR-WP07A_FORMAL_RUNBOOK.md)、[部署方案](CR-WP07A_LOCAL_DEPLOYMENT_PLAN.md)、[安全复核](CR-WP07A_SECURITY_REVIEW.md)、[测试规范](CR-WP07A_TEST_SPEC.md) 及 [冻结指纹](baseline/cr-wp07a-fingerprints.json)。失效的 [实施审批契约](archive/cr/CR-WP07A_IMPLEMENTATION_CONTRACT.md) 已归档。[文档索引](README.md#最新验收和部署证据) 列出项目内保留的隔离验证资料。

## 责任分工与运行复核

2026-11-08，负责人确认由本人使用长期 ADMIN、保管应用及部署凭据，并接收和响应 Windows 巡检。正式账号通过已验收的应用本机管理方法创建，已核对 ADMIN 角色和启用状态。初始凭据由负责人在本机保管。首次登录由负责人在本机完成。

本次只读复核确认四个实际镜像 ID、五个正式卷、E 盘 Compose 哈希及回环绑定均与移交基线相符。维护为 OPEN，producer-ready 正常，Windows 巡检无故障。删除控制保留 2 条意图，数据库保留 2 项已完成清理、4 条 90 天删除审计和 12 条不透明的已删除定位。本次无到期删除审计、待处理或阻塞清理及未解决告警，按现行保留规则处理。

WP07-A 验收时，巡检随当前 Windows 用户登录启动。当前状态见上方巡检小节。新增业务数据备份和超出本机的访问暂缓。此处的离线模型状态记录 WP07-A 阶段。[S3-11 探针验收](archive/s3/S3_11_PREFLIGHT.md)已归档，当前模型设置见[S3-11 操作指南](S3_11_USER_GUIDE.md)。

## S3-11 本机软件更新记录

S3-11 候选 Worker 和 Web 已安装，部署时默认保持离线，现有 API、数据库、对象存储及持久卷沿用 WP07-A。现行更新与配置见 [S3-12 本机运维](../deploy/README.md)。本页的 WP07-A 镜像表保留为原验收和回退基线。

## 后续工作

- 负责人完成长期 ADMIN 的首次登录。桌面通知依赖当前 Windows 用户登录。
- 保留已验证镜像和正式持久卷，按应用规则管理删除控制与审计。未来更换镜像时重新核对冻结输入和实际镜像 ID。
- 超出本机的访问与新增业务备份按独立审批处理。
