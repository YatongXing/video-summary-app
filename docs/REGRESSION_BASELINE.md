# 非模型回归与本地容量基线

本报告是 S3-10 实施证据，与 [AC](AC.md)、[E2E_TEST_PLAN](E2E_TEST_PLAN.md) 分工：需求文档定义预期，本报告说明测试到哪里。需求、正式质量阈值和 SLO 不因这些测量改变。测试替身不调用真实 Qwen，不证明语义、翻译质量或模型 P90。

## 运行记录

| 验证层次 | 本轮记录 | 证据与限制 |
| --- | --- | --- |
| 后端单元/真实 PostgreSQL 集成 | 主 Agent 已执行 236 项通过，80.05 秒 | 使用独立 `_test` 数据库与逐测试 schema；真实 FFmpeg 短合成媒体，模型为离线替身。 |
| 前端构建与浏览器交互 | TypeScript/Vite 构建通过；Chrome 7 类交互检查通过（7.68 秒） | [ui-smoke.mjs](../frontend/tests/ui-smoke.mjs)，本地模拟 API；不替代服务端授权或视频解码测试。 |
| 真实 HTTP/独立 Worker/私有对象存储闭环 | 通过，8 类闭环检查；自身资源清理无错误 | [local_baseline.py](../backend/tools/local_baseline.py)；只在独立 schema、唯一私有 bucket、临时目录中运行。 |
| 本机小负载容量 | 通过本轮短时负载，数值与限制见下表 | 5 个并行上传请求、10/30 个已认证使用者的交互；不得改称 30 个同时上传或真实模型并发。 |
| 故障注入 | 纳入后端回归 | [test_system_failures.py](../backend/tests/test_system_failures.py)；真实子进程退出/存储或结果发布故障等以具体用例为准。 |

本轮 HEAD 为 `dc257d148d2033b2d65a3470e11ae66a7ff50fe1`，分支 `implementation`，含 S3-07～10 未提交修改，单凭 HEAD 不能复现当前源码。机器证据保存 73 份相关源文件的 SHA-256 和组合指纹 `9e6d0cca7c88dee6a119aab2c3ffb6b836b11a35dd3f54a041f167890cba8480`，不含真实环境文件。下面映射表示存在对应自动检查，不表示每条复合 E2E 的全部真实环境环节均已验收。未用覆盖率工具，不报告覆盖率百分比。

## 需求与回归追踪

测试文件统一位于 [backend/tests](../backend/tests/)。表中函数名为可检索的代表用例，不是把参数化实例重复计算为独立产品能力。

| E2E / AC | 可重复证据 | 尚未覆盖或证据边界 |
| --- | --- | --- |
| 01 / AC-01 | `test_auth.py::test_registration_persists_hash_and_login_logout`、`test_registration_rejects_escalation_and_normalizes_email`、`test_expired_session_is_rejected`、`test_deployer_admin_creation_and_disable` | 部署者最终管理员账户交付在 S3-12；测试仅用合成账户。 |
| 02 / AC-02 | `test_auth.py::test_anonymous_cross_user_and_deleted_task_access`；`test_ingest.py::test_inputs_require_login_csrf_and_rights`；`test_results.py::test_result_export_owner_isolation_and_admin_metadata` | 内容权限以服务端测试为依据，不依靠前端隐藏。 |
| 03 / AC-03 | `test_media.py::test_actual_h264_aac_supported_containers`、`test_silent_video_and_actual_content_instead_of_extension`、`test_exact_byte_and_duration_boundaries` | 边界用例使用受控属性/缩小字节阈值；未上传完整 2 GB / 2 小时文件，也未证明全部实际媒体变体。 |
| 04 / AC-04/06 | `test_ingest.py::test_disconnected_upload_removes_partial_staging`、`test_failed_object_cleanup_remains_registered_until_recovery`、`test_valid_upload_and_url_become_owned_queued_tasks_without_spend` | 真实大文件断网和家庭局域网设备中断未据这些短样本验收。 |
| 05 / AC-04 | `test_tasks.py::test_recovery_anchor_not_reset_by_later_use_and_stops_at_cap`、`test_refund_after_natural_recovery_never_exceeds_three` | 使用可控 UTC 时钟；不等待实际六小时，也未自动切换操作系统时区。 |
| 06 / AC-05 | `test_tasks.py::test_failure_categories_and_no_retry_recharge`、`test_two_workers_and_repeated_failure_only_spend_refund_once`；`test_system_failures.py` | 外部模型失败通过替身模拟；真实供应商费用和失败行为仍待批准测试。 |
| 07 / AC-05/09 | `test_tasks.py::test_cancel_failure_race_has_one_final_category_and_settlement` | 证明受测数据库交错，不声明所有机器调度排列都已穷举。 |
| 08 / AC-07 | `test_tasks.py::test_retry_fifth_triggers_cooldown_rejections_do_not_extend_or_count`、`test_exact_rolling_window_lower_boundary_excluded` | 可控时钟验证滚动边界。 |
| 09 / AC-07 | `test_tasks.py::test_retry_twenty_limit_and_old_request_key_replay` | 重复请求及累计上限，不产生真实 API 请求。 |
| 10 / AC-08 | `test_tasks.py::test_cooldown_staircase` | 配置阶梯与拒绝请求；不实际等待每个冷却时长。 |
| 11 / AC-09 | `test_tasks.py::test_queue_cancel_processing_cancel_and_success_finality`；`test_results.py::test_pipeline_cancellation_before_publication_prevents_results` | 合作取消与迟到提交；不承诺能撤销供应商已经完成的工作。 |
| 12 / AC-10 | `test_queue.py::test_fifo_user_and_task_ties_and_admin_no_priority`、`test_only_active_user_can_be_bypassed_and_oldest_runs_when_free`、`test_concurrent_claims_enforce_global_cap_and_spend_once` | PostgreSQL 并发与配置限制已检查；不是未来负载下无饥饿的无限期证明。 |
| 13 / AC-11 | `test_migrations.py::test_time_fields_are_timezone_aware`；`test_results.py::test_queue_processing_times_and_target_have_honest_semantics` | 存储 UTC 和排队/处理时间分离；浏览器格式本地化不改写 UTC。 |
| 14 / AC-16/17 | `test_privacy_content.py::test_admin_needs_exact_current_task_purpose_scope_and_access_is_audited`、`test_video_consent_revoke_during_object_read_stops_next_chunk`、`test_deleted_task_blocks_previously_authorized_admin_content` | 流读取中的撤销采用受控存储流；已下载副本无法收回。 |
| 15 / AC-11 | `test_results.py::test_queue_processing_times_and_target_have_honest_semantics`；`frontend/tests/ui-smoke.mjs` | 浏览器实际 React 页面与模拟 API；五状态、Processing 进度条无 value、Queued 0/Completed 100、处理完成仅刷新且无 `alert` 已在浏览器验证。视频播放/时间戳跳转未作解码验收。 |
| 16 / AC-14/15 | `test_results.py::test_invalid_content_cannot_become_export`、`test_only_explicit_whole_json_fence_repair_is_allowed`、`test_pipeline_invalid_second_language_fails_refunds_once_and_cannot_download` | Schema/结构/退款门槛；不能由合法 JSON 推断事实准确。 |
| 17 / AC-13/14 | `test_model_adapter.py::test_fake_consumes_both_modalities_without_network`；`test_preprocess.py::test_all_audio_tracks_are_retained`；`test_results.py::test_schema_exact_export_and_separate_presentation` | 双语结构及模态证据管道，不是中英文语音理解或翻译质量评测。 |
| 18 / AC-18 | `test_cleanup.py::test_thirty_day_expiry_covers_all_states`、`test_expired_task_bodies_and_logs_are_pruned_despite_external_cleanup_failure` | 通过 UTC 时钟/数据时间模拟，不等待 30 天。 |
| 19 / AC-19/20 | `test_cleanup.py::test_linked_evaluation_artifact_is_not_exempt`、`test_failed_cleanup_escalates_after_three_retries_not_first_attempt`、`test_long_worker_lease_does_not_postpone_cleanup_deadline_alert`；`test_deletion_control.py` | 删除栅栏/关联资产/失败升级/恢复重放有证据；真实磁盘、整库备份清除与恢复演练未完成，备份未核验时保持 PENDING。 |
| 20 / AC-12 | `test_results.py::test_queue_processing_times_and_target_have_honest_semantics`；本地 runner 的时间样本 | 本轮短合成视频和离线替身不能用于判定 60 分钟视频成功任务 P90 ≤20 分钟；真实目标仍待 S3-11。 |
| 21 / AC-22 | 本轮未执行 | 15 个授权视频由产品负责人在 UI 手动基线评测，±5 秒是初始时间戳容差；不自动设置正式质量阈值。 |
| 22 / AC-21 | `test_safe_download.py::test_all_dns_answers_must_be_public`、`test_pinned_socket_preserves_original_tls_identity`、`test_redirect_revalidates_target_before_connection`、`test_relative_redirect_and_no_credentials_forwarded`；`test_ingest.py` | DNS/TLS/响应/重定向使用受控替身；未据此声称真实公网 HTTPS 视频来源、模型回连路径或平台页面集成通过。 |

## 重跑方式与隔离

后端沿用 [运行说明](../deploy/README.md) 的测试镜像和显式 `TEST_DATABASE_URL`，数据库名必须以 `_test` 结尾。完整回归使用 `python -m pytest -q -p no:cacheprovider`。不要在测试运行期间重建其 PostgreSQL 容器；API/Worker 的最终构建与启停须与整库回归顺序执行。

前端从 `frontend/` 执行 `npm run test:ui`：先构建，再运行 Node 内建 CDP 客户端；无需安装 Playwright、额外依赖或下载浏览器。默认查找已安装的 Chrome/Edge，可通过 `UI_TEST_BROWSER` 指定浏览器。Node 20 使用内建 `--experimental-websocket`。脚本以临时独立浏览器配置和本机回环静态/模拟接口服务运行，结束后关闭自身进程并清理临时配置。下载点击被拦截，不写入用户下载目录，不使用用户浏览器会话。

系统 runner 显式接收独立测试库、测试对象存储连接和输出路径，不加载 `.env`。每次新建唯一 schema/bucket/目录，启动自己的 API/Worker；退出清理只限这些资源。失败也必须保留失败阶段和有限、无秘密的诊断。实际命令与环境记录由主 Agent 在下面补齐。

## 容量测量口径

分别报告 5 个同时发起的上传请求，以及 10/30 个预先认证的用户访问任务/额度等界面接口的场景。报告实际请求数、成功/失败数、持续时间、原始耗时、资源采样和有效并发配置；同一用户反复请求不等同于独立多用户。

请求耗时与任务排队、实际处理耗时分别报告。小样本延迟中位数及 P90 用于本机回归比较，不作为新 SLO；runner 的 P90 使用最近秩 `ceil(0.9 × n)`。短时抽样峰值不是全程峰值，未采样到等待不等于从未发生等待。模型替身、短媒体、预热状态、容器资源限制和读请求比重都会影响结果。

即使本轮 30 位使用者读接口无失败，也不能推断 30 个长视频同时上传、处理或真实 Qwen 调用可行。PostgreSQL 队列继续首发的依据应结合本轮是否出现领取/退款错误、长期锁等待、积压与资源不足；发现瓶颈再提出改变，不因测试工具而先引入 Broker。

### 本轮环境与容量结果

测量 UTC：2026-10-30 15:22:56～15:23:19。原始请求耗时、任务 UTC 时间、63 次资源采样、配置、源文件指纹和清理结果见 [local-baseline.json](baseline/local-baseline.json)。本轮状态 PASSED，退出码 0，资源清理错误为空。首轮在测量前因工具把损坏媒体的合法 422 拒绝响应限定为 400 而停止；修正测试预期并确认未建任务/未扣额后重跑，未修改产品行为。

| 环境 | 实际记录 |
| --- | --- |
| Windows | Windows 11 家庭版中文版，10.0.26300 |
| 本机 | Ryzen 7 5800H，8 核/16 线程；约 32 GB 内存；RTX 3060 Laptop（本轮不用于模型推理） |
| Docker | 客户端/Engine 29.9.2，Compose 5.6.1；WSL2 Linux；VM 16 逻辑 CPU、16,693,231,616 字节内存（约 15.55 GiB） |
| 运行时 | Python 3.11.18；Node 20.20.2、npm 10.8.2；PostgreSQL 17 Alpine、RustFS 1.0.4 |
| 测试配置 | 全局处理并发 1、导入并发 5；Worker 轮询 1 秒、清理轮询 5 秒；外部模型调用关闭、离线替身 |
| 媒体 | 3 秒、320×180、H.264/AAC MP4，82,171 字节；5 个上传请求使用相同合成输入 |
| 桌面条件 | E 盘测试前约 336 GB 可用；未控制无关桌面负载，非专用性能环境 |

| 场景 | 实际样本 / 成功 / 失败 | 请求中位数 | 请求 P90（最近秩） | 持续时间与含义 |
| --- | --- | --- | --- | --- |
| 5 位用户同时上传 | 5 / 5 / 0 | 1.812 秒 | 1.821 秒 | 同步释放 5 个请求；含实际校验/私有存储/入队，不含排队后视频处理 |
| 10 位已认证用户 | 60 / 60 / 0 | 77.2 毫秒 | 88.6 毫秒 | 0.472 秒；每人 3 轮 tasks/quota 两个请求，未测登录突发 |
| 30 位已认证用户 | 180 / 180 / 0 | 236.0 毫秒 | 260.2 毫秒 | 1.428 秒；同上，为短时读接口负载，不是 30 个视频上传/处理 |

5 个成功的合成视频任务实际处理时长中位数 0.848 秒、P90 1.307 秒，排队另计；这些值只用于检查离线应用计时和未来同条件回归，**不能用于判定 60 分钟视频的 20 分钟目标**。

资源采样约每 0.2 秒，从认证完成后至删除验证结束共 12.515 秒、63 次。采样观察到最多 5 个活动导入、1 个处理中任务、5 个排队任务；未采样到应用连接的 PG 锁等待。测试容器的内存采样峰值约 544.4 MiB，CPU 累计增量对应平均约 1.02 个核，I/O 计数增量写入 20,480 字节、读取 0。零读取可能来自缓存；这些是测试容器计数，包含 runner/API/Worker/FFmpeg，不含 PostgreSQL/RustFS、整个 Docker VM 或 Windows 全机资源，不能用来推算整机磁盘吞吐或长视频空间需求。短时采样也可能漏掉瞬时峰值。

本轮未观察到重复扣退、领取/权限错误或足以要求增加 Broker 的瓶颈，故没有改变 PostgreSQL 首发队列方向。下一次比较应使用相同媒体、默认配置、预热/认证条件和机器资源；长视频、持续负载、真实 Qwen、磁盘/队列积压仍需后续测量。

## 停止点与未完成事项

本阶段只完成非模型回归与本机初始测量。S3-11 的模型接入/计费批次、真实素材外发及性能验证没有获本轮自动执行授权；S3-12 的真实备份介质清单、清除和恢复演练仍未完成。后续阶段须由产品负责人另行启动。


## 新增回归资产与运行命令

- [test_system_failures.py](../backend/tests/test_system_failures.py)：结果事务延迟至 COMMIT 失败时双语结果/成功状态整体回滚且仅退款一次；私有存储故障不调用模型/开放导出；实际独立 Worker 子进程强杀、恢复及旧代次拒绝。
- [ui-smoke.mjs](../frontend/tests/ui-smoke.mjs)：7 类浏览器交互回归，包含五状态与完成不弹窗；`npm run test:ui` 作为固定入口。
- [local_baseline.py](../backend/tools/local_baseline.py)：可重复的隔离真实 HTTP/私有 S3/独立 Worker 闭环及小负载计时；原始 JSON 为比较基点。

以下从项目根目录的 PowerShell 执行，仅使用无密钥示例和专用测试库，不读取根目录 `.env`。首次创建测试库后，后续运行复用它；若已经存在，不重建或删除数据库。命令中 `local-dev-only` 是示例的本地开发凭据，不能用于开放局域网或生产部署。

```powershell
# 先启动依赖；不要在测试或测量运行中重建它们。
docker compose --project-directory deploy --env-file deploy/.env.example -f deploy/compose.yaml up -d db object-store
# 仅首次执行；已存在则跳过。
docker compose --project-directory deploy --env-file deploy/.env.example -f deploy/compose.yaml exec -T db createdb -U video_summary video_summary_s310_test
docker build --target test -t video-summary-app-test -f backend/Dockerfile backend
docker run --rm --network video-summary-app_private -e PYTHONPATH=/srv -e TEST_DATABASE_URL=postgresql://video_summary:local-dev-only@db:5432/video_summary_s310_test video-summary-app-test python -m pytest -q -p no:cacheprovider

# 与容量测量顺序执行，避免浏览器/回归负载干扰测量。
npm --prefix frontend run test:ui

# 新测量另存，保留本轮 local-baseline.json 以便比较。
$projectRoot = (Get-Location).Path
docker run --rm --network video-summary-app_private --mount "type=bind,source=$projectRoot/backend/tools,target=/srv/tools,readonly" --mount "type=bind,source=$projectRoot/docs/baseline,target=/output" -e PYTHONPATH=/srv -e TEST_DATABASE_URL=postgresql://video_summary:local-dev-only@db:5432/video_summary_s310_test -e S3_ENDPOINT_URL=http://object-store:9000 -e S3_ACCESS_KEY=local-dev-only -e S3_SECRET_KEY=local-dev-only video-summary-app-test python tools/local_baseline.py --output /output/local-baseline-new.json

docker compose --project-directory deploy --env-file deploy/.env.example -f deploy/compose.yaml down
```

不加 `--volumes`，保留现有数据卷和删除记录。Runner 自行清理它创建的唯一 schema、bucket、子进程和临时目录；无法完成时报告 cleanup_errors 并以非零退出，不清理其他项目数据。当前 73 文件指纹由本轮主 Agent 在实际测量后记录，重跑时应另记录新源码版本和未提交状态，不将新的报告冒认为当前基点的原样复现。
