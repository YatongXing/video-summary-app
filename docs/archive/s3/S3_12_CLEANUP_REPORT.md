# Video Summary App 文件清理与剩余盘点

复核日期为 2026-11-09。正式项目的更新入口为 `deploy/Update-VsaLocal.ps1`。

## C 盘 Work 工作区

| 清单 | 已移除的原相对路径 |
| --- | --- |
| C1 | `s312-coding` |
| C2 | `s312-test` |
| C3 | `s3-11-worker/frontend/node_modules` |
| C4 | `s312-build/frontend/node_modules` |
| C5 | `s3-11-worker/oss-linux-wheels` |
| C6 | `s3-11-worker/oss-wheels` |

其余 18 个顶层项目项已移入 `docs/archive/work-private/s3-12-2026-11-09/work-s3-12.zip.dpapi`。原位置只保留项目说明 `AGENTS.md` 和只读同步目录 `sources/`，无待清理项目材料。

归档包含 247 个文件、35 个目录，使用当前 Windows 用户的 DPAPI 保护。加密包、`manifest.json` 和 `Restore-WorkArchive.ps1` 存放在同一个私有归档目录，Git 会忽略整个目录。默认运行恢复脚本时，只解密并按 manifest 校验。需要恢复时，用同一 Windows 用户在 PowerShell 7 运行该脚本并提供 `-OutputDirectory` 指向私有 NTFS 文件夹下的一个新目录。迁移后已使用此方式完整恢复并逐文件复核。归档包 SHA-256 为 `26e42985319916722e149dd04c50dc0800a723002ff251c78bdd3d8877ccedf9`。

## E 盘 Records

以旧 Records 的 WP07-A 目录为基准，以下八个文件均已移除。

| 范围 | 已移除的原相对路径 |
| --- | --- |
| E1 | `deployment/deploy/compose.wp07.yaml` |
| 旧设置索引 | `deployment/s3-11-settings.json` |
| 原证据 1 | `cleanup-verification.json` |
| 原证据 2 | `deployment-verification.json` |
| 原证据 3 | `evidence-manifest.json` |
| 原证据 4 | `Restore-WP07ARecords.ps1` |
| 原证据 5 | `WP07-A-evidence.zip.dpapi` |
| 原证据 6 | `WP07-A-最终整理与移交报告.md` |

E1 在仓库和 S3-12 交付包的两份保留副本均匹配 SHA-256 `e44683998b7421e8def837d3de79bcc9ec0c9ed090ca5c119a126bf652965e6e`。现行更新由正式容器的 Compose 标签定位私密配置。旧设置索引仅有退役入口使用。验收结果、原归档摘要和校验信息见 [WP07-A 部署移交](../../WP07A_DEPLOYMENT_HANDOFF.md)。原 Records 树当前只有空目录，可后续移除空目录。

## 正式仓库剩余盘点

| 路径 | 处理方式 |
| --- | --- |
| [旧入口与专用配方](retired-deploy/) | 已一同归档。旧入口依赖已移除的设置索引。 |
| `.pytest_cache/`、`frontend/node_modules/` | 可重建的本地缓存，按需要清理。清理依赖目录后，下次从源码构建需重装依赖。 |
| `frontend/dist/` | 保留。WP07-A 三个冻结文件的 SHA-256 匹配，`deploy/Dockerfile.web` 仍以此为构建输入。 |
| `deploy/Dockerfile.s3-11-worker` | 保留。已有 Worker 构建配方，可用于后续复现。 |
| `release/s3-12-local/`、`docs/archive/work-private/` | 分别保留正式交付包和旧工作区加密归档。 |

本轮仓库改动限于 `.gitignore`、`docs/README.md`、`docs/WP07A_DEPLOYMENT_HANDOFF.md`、`docs/IMPLEMENTATION_PLAN.md` 和 `docs/archive/s3/S3_11_PREFLIGHT.md`。清理报告归入 S3 历史文档，私有归档属于本机忽略目录。

正式更新预演通过。`vsa-local` 的五个容器和五个正式卷均在，就绪接口返回 HTTP 200，Web 仅发布在 `127.0.0.1:58080`。