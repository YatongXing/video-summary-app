# CR-WP01-C：删除、清理与维护设计

> 删除、清理、审计和部署者维护规则继续有效。下文涉及排障授权的处理仅用于历史记录关闭、审计和清退。现行产品权限见 [CR-002](CR-002.md)，正式部署身份和门禁见 [WP07-A](CR-WP07A_LOCAL_DEPLOYMENT_PLAN.md)。

**版本：** L1.0 · **日期：** 2026-11-04
**状态：** R-03/R-04批准规则保留；WP03已验收、WP04已单独授权实施，具体字段/接口冻结见§7，运行证据见[WP04报告](archive/cr/CR-WP04_VALIDATION_REPORT.md)。本文件替代旧claim/多状态/ops回执方案；未自动认定WP04验收或授权后包。
**关联：** [共享契约](archive/cr/CR-WP01_CONTRACT.md)、[数据/API](archive/cr/CR-WP01_DATA_API_DESIGN.md)、[切换](archive/cr/CR-WP01_CUTOVER_ROLLBACK.md)。

## 1. 删除接受与内容边界

有效会话/CSRF/所有权检查，管理员只能删除自己任务。源用户→任务锁序；先原子持久化/fsync独立无内容删除意图，同事务任务删除栅栏/lease_generation失效、取消排队/后续重试、关闭当前support_request、必要support_version+1、创建/复用job与事件。已有job早返回/重放也补齐授权失效。

提交后202仅接受，任务移出列表、所有owner/admin内容/导出/续传拒绝。意图已落盘而SQL失败503/结果未知，仍挡读取/生成/发布并重放补齐，不能移除意图恢复访问。排队不扣，处理中主动删除按原取消不退已扣额度，幂等不重复结算；30天到期也同步终止请求。

覆盖原片、音频、帧/截图/片段、临时文件、工作区、双语摘要/章节/事件正文、SQL/JSON/内容型导出、反向评测资料。只有应用实际受控资产逐项验证完成才成功，不影响其他任务；不承诺下载/供应商/介质历史字节收回。

## 2. R-03：最小生产停止候选与硬门槛

保留checkpoint正常取消、受控Popen停止/kill/wait，线程返回、父退出、lease超时不是全部生产者停止证明。不新增容器调度/复杂进程管理系统。

Linux Docker本地共享控制卷locks子目录，每task稳定inode：生产者SH覆盖目录创建、下载/预处理及所有可能写入的子/后代；FFmpeg/ffprobe显式pass_fds继承同一open-file-description，父不得提前LOCK_UN。生产先SH再短DB/fenced_write，queue领取事务先结束；不能持DB行锁等待flock。

清理非阻塞取task EX，拿不到则等待/诊断，不清物理资产/不标完成。拿到后仍复核归属、代次/意图与可信登记。锁文件不在待清理工作区，不在运行时unlink/重建。recover_workspaces/遗留目录清理共用规则；finally仅清自己已停子进程的独占工作区，不宣告整任务完成、不绕过跨任务隔离。

WP04必须真实Linux Docker双容器/共享本地命名卷、实际FFmpeg/ffprobe验证父SIGKILL子仍写、FD继承、稳定inode/替换负例、清理互斥、并发生产、旧I/O、重启/暂停/DB失联及锁序。失败停止相关实施、提交替代方案，不以lease/空目录降级。未知旧生产者需先隔离/排空，WP07确认旧服务及后代已停。

生产系统全局并发N仍可配置，普通用户同时1个、FIFO/唯一越过及配额/退款/重试不变；N>=2的跨用户生产、删A不伤B必须实证。全局清理1只限清理I/O，不将视频处理串行化。停止无法证明时保留不可读/不可发布和未完成信号，超30天实际未清需报告未达标。

## 3. 四态、最小控制与执行（WP01规则，实施字段见§7）

持久四态：PENDING、RETRY_WAIT、BLOCKED、COMPLETED；RUNNING/等待已知生产者只作锁与进度观察。BLOCKED原因至少区分CONFIG、UNSAFE、LEGACY_APPROVAL、PRODUCER_UNKNOWN，不自动解除危险状态。

保留job身份/任务归属、原始started_at、attempt_count/连续暂时失败次数、last_physical_started_at/next_attempt_at、脱敏错误、单job_version；保留online_completed_at与整体completed_at、可信资产定位、受控BACKUP审批和逐资产验证。job_version仅保护陈旧维护更新，不是租约/停止证明。取消claim_token/claimed_at/claim心跳/deadline、ops独立回执、controller表及整套v2影子列；不在WP02冻结其SQL字段。

全局稳定清理EX→task EX→源用户/任务/job短DB；全局锁覆盖完整I/O/验证，所有后台清理入口共用，后继者必须真取锁，不能凭DB断连/超时接管。生产只task SH→短DB，无反向等全局锁；不持DB行锁跨网络I/O。

取可信不可变资产定位→外部幂等删除→实际验证→短事务重新锁job、读当前版本/状态，不能用I/O前快照覆盖调度/BLOCKED变更；结果仅写已核验资产。DB不可用不记成功，恢复后重验。已成功资产不因另项失败回滚，SQL正文可独立先清。

旧originals/user/importUUID不含task仍可用可信复合登记核实；不能机械拒绝或猜前缀。错误backend/bucket/key/version、路径逃逸/链接/未知生产者停止涉及删除；NoSuchBucket是配置错误，不是目标不存在。版本化对象须核实受控版本，delete marker不足；目录要安全根、非跟随链接和生产停止证明。后台导入暂存/工作区回收也须明确生命周期及归属，不整bucket清空。

历史BACKUP未批准时继续安全在线清理；仅在线已完成且剩余唯一障碍为历史批准，才BLOCKED/LEGACY_APPROVAL。overall完成含全部适用受控资产，online完成不替代；未知基础设施副本另记未核实。旧COMPLETED须重核，旧MANUAL_REQUIRED按原因接管，不批量改成功/重置原时间和已知次数。接管算法/结构在WP04验证，真实执行留WP07另授。

## 4. 持久重试与最低故障发现

| 项 | 保留的初始参数/语义 |
| --- | --- |
| 轮询/首次 | poll5秒；首次尽快调度但不绕过生产停止，每轮先非force重放独立intent，SQL提交失败可在后续轮补齐而无需重启。 |
| 暂时失败 | 第n轮失败后min(3600,60×2^(n-1))秒，加0～20%正jitter后封顶3600；每轮最多加n一次。 |
| 频率/并发 | 同job物理轮次至少60秒；单机全局清理1；CLI/重启不得重置。 |
| 执行预算 | 每轮20资产或30秒后不启新I/O（逐次外部调用检查，不只在资产边界）；续轮不算失败，部分成功/预算耗尽不清失败n。全部当前可执行在线资产无错验证/在线完成才归零。 |
| S3 | connect3/read10秒，关闭隐藏重试，删除/验证10调用/秒burst10；成功资产不重复删。 |
| 持续故障 | 第5连续暂时失败或从请求>30分钟未完任一；危险/配置立即信号。 |
| 进度/发现 | 独立health看真实last_loop_completed/active_io_started，>60秒无进度、缺失/读错/依赖未知均非健康；重复信号最多5分钟一条，维护循环异常同类日志也至少300秒间隔。 |
| 期限 | 任务自创建30天；删除审计各事件90天到期首次维护轮次清，延迟可见不可续期。 |

TRANSIENT自动退避，CONFIG核验后受控重排，UNSAFE/未知归属停止；已知生产锁忙只等、不加存储失败n。既有cleanup_alerts+结构化脱敏日志+独立health足够，不建告警服务。控制卷runtime子目录原子进度文件不放journal根部*.json，避免被重放器误读；活性心跳不等于扫描进展。

WP04交付发现入口，WP06注入卡扫描/I/O/DB/日志故障验证，WP07开放前必须有实际运行的health/60秒巡检渠道及明确部署者；没有观察器不能称自动通知。S3-12仅完善，不推迟最低发现链。问题实际重验解决才关闭，确认告警不等清理成功。

## 5. status/health/retry维护入口（批准规范，实施证据见WP04）

| 未来入口 | 权限/副作用 |
| --- | --- |
| status --job-id UUID [--details] | 合并诊断，只读状态/内部ID/分类/版本/计数/时间，不输出内容、凭据或任意路径。 |
| health | 只读活性/真实进度/持续故障与依赖未知；不把连接成功当健康。 |
| retry --job-id UUID --expected-version N --reason STORAGE_RECOVERED | 维护环境重新核验后仅安全重新排队，不同步强删。 |

运行/维护两类DB权限，不另设默认ops角色。maintenance凭据只给受控一次性环境，不挂API/Worker、不由普通管理员Cookie调用；health可用运行身份作只读查询。维护owner是受信部署者，工具不能宣称数据库限制owner直接SQL；不冒称逐人审计，记录可证明部署实例/维护角色/容器UID。

retry先非阻塞全局清理锁再短DB锁，核expected job_version与可恢复配置阻塞；已排队/等退避不提早next_attempt、不重置首次时间/失败数。实际重排、版本+1和90天无内容审计同事务，审计失败全回滚/无先行I/O。重复/陈旧操作无额外副作用，版本冲突后status，不承诺精确历史原响应。

UNSAFE、未知生产者、历史待批不能retry直接解除；修复/批准须证据和专项授权。无force/mark-complete/skip-owner-check/path/key/bucket/任意SQL。命令成功只表示检查或调度，不代表实际清完。退出码沿0确认、2需处理/安全拒绝、3参数、4依赖未知、5版本冲突。

## 6. 审计、完成与历史能力

每事件90天保留最小必要ID快照、操作/事件及请求/完成时间、结果/错误分类，无视频/摘要/文件名/凭据。未完成job/资产最小控制至完成或专项解决，不冒充成功；完成后收缩不再必要定位与内容型详情。

独立删除意图/identity/重放仍保留，取消备份不删除deletion_control卷。历史副本先清点隔离核实、专项批准清理，验证后才申请退役兼容；不把BACKUP_INVENTORY_CONFIRMED改true消除未知。原历史删除后30天义务不擅延，未达标报告。

WP01阶段仅同步批准设计；现WP04按R-04完成字段冻结并实施，实际运行证据与未测边界独立记入WP04报告，不将WP01文档验证追记为运行通过。

## 7. WP04实施冻结（2026-11-05，技术落实）

用户已正式验收WP03并授权WP04；本节落实已批准R-03/R-04，不改变产品语义。真实业务迁移/历史批准/恢复开放仍留WP07。011编号启动时未占用，001～010冻结。

### 7.1 数据与兼容

- 011_cr_cleanup.sql原位收敛cleanup_jobs：保留id/task_id/user_id/started_at/attempt_count/next_attempt_at/completed_at/last_error_kind/last_error_code；增加job_version bigint默认0、consecutive_failures integer默认0、last_physical_started_at、online_completed_at、blocked_reason。删除last_attempt_at（映射到last_physical_started_at）、worker_safe_after、manual_requested、backup_cleanup_state、backup_completed_at；无claim/影子/第二状态机。
- 四态PENDING/RETRY_WAIT/BLOCKED/COMPLETED；BLOCKED原因CONFIG/UNSAFE/LEGACY_APPROVAL/PRODUCER_UNKNOWN。COMPLETED必须online_completed_at及completed_at存在，其他状态completed_at为空；计数/版本非负。所有执行、维护更新推进单job_version，I/O后重锁重读，版本变化不能覆盖新调度/阻塞；不以版本代替物理锁。
- 旧PENDING保留；旧RUNNING转BLOCKED/PRODUCER_UNKNOWN；旧MANUAL_REQUIRED按原因接管：UNKNOWN_ASSET/INVALID_ASSET_KEY等明确危险转BLOCKED/UNSAFE，ASSET_CLEANUP_FAILED/OBJECT_STILL_PRESENT等已知暂时故障转RETRY_WAIT（next不早于旧next、last_attempt+60秒和当前时间），仅时限升级且无错误转PENDING，未知非空分类转BLOCKED/UNSAFE；原时间/已知次数不清零，consecutive_failures保守承接attempt_count。旧COMPLETED转PENDING重核。旧作业资产重新登记待验证，不把旧标记当物理oracle。修改这些控制状态只在隔离合成迁移中测试，真实接管留WP07。
- media_assets保留唯一storage_key与复合task/user FK；新增storage_backend、storage_bucket、storage_version、cleanup_approved_at。backend取DB或绑定安全位置的WORKSPACE:<root path/dev/inode SHA256>、S3:<规范endpoint SHA256>；旧裸WORKSPACE/S3表示尚未核实，不能用当前配置悄悄补齐。DB/WORKSPACE不填bucket/version；新S3上传登记实际backend/bucket及可确认version，旧S3的未知bucket/version不得凭当前环境静默补齐。运行时不能改资产id/task/user/kind/key/backend/bucket/version；只允许清理进度变化。BACKUP批准仅受控维护owner可登记，INSERT/UPDATE都检查，普通服务和retry不能批准；owner核实legacy定位后的一次受控补齐须专项证据，不能提供HTTP或retry捷径。
- 可信既有复合登记在迁移时冻结；发现同用户其他任务key、归属/位置变化、未知backend/bucket/版本/链接均拒危险删除。originals/user/importUUID按真实旧来源核验，不机械要求task前缀；新登记端验证其本任务关系，外部对象操作必须和冻结登记一致。
- input_imports同样新增冻结storage_backend/storage_bucket/storage_version及staging_backend，begin_import记录；存储前再次核验，cleanup不拿当前环境猜旧位置，旧NULL为CONFIG且health可发现。导入id/user/object_key/定位tuple不可运行时改写，无新表。
- 历史BACKUP行保留，未有逐资产批准不物理删除；不再依赖BACKUP_INVENTORY_CONFIRMED=true。未知基础设施副本另行核实，不能填“不存在”或堵住所有新无BACKUP任务的应用资产完成。

### 7.2 生产与物理锁接口

新增app/task_locks.py：producer(task_id)取得SH，exclusive(task_id)非阻塞EX，cleanup_global()非阻塞全局EX；成功返回可上下文使用的句柄，忙明确返回/抛LockBusy，依赖/归属失信抛LockUnsafe。inherited_fds()仅提供当前生产作用域FD，供preprocess子进程pass_fds。

- task SH覆盖整个processor，包括目录创建、下载、FFmpeg及后代；Worker领取提交后才等SH，生产不得持DB锁等flock。每线程/异步作用域FD隔离，不串行化多task。
- 控制卷locks下锁文件永久保留、只close不LOCK_UN，防父进程解锁仍在运行子进程继承的同一open-file-description。稳定硬链接anchor与fstat/lstat比对检测锁文件替换；拒链接逃逸，不unlink/重建anchor。该最小inode检查不承诺抵御宿主root改全部权威。
- 预处理/校验统一受控Popen kill/wait，所有FFmpeg/ffprobe继承FD。正常finally只清本次已停止子进程的独占工作区；恢复/整任务清理必须EX并复合归属复核。导入暂无task时用独立import-UUID SH/EX，生命周期含上传/下载/校验/存储；回收不能凭TTL清活生产者。
- 全局EX→task EX→源用户/task/job短DB；锁覆盖外部I/O和验证，不持SQL行锁跨网络。旧I/O/DB断线/超时均不能让第二者接管物理工作。
- 旧producer没有新锁的事实不能靠空闲EX掩盖：控制卷runtime/producer-ready文件绑定deployment_id，仅在部署者确认旧服务及后代已排空后离线建立；缺失/失信为PRODUCER_UNKNOWN，不能物理清理。测试夹具明确创建合成证明，程序不自动生成ready。正式建立/验证留WP07。

### 7.3 清理/维护冻结

process_job(job_id)保留Python入口但manual不得绕过；run_once()后台轮询。请求202只返回{id,task_id,accepted:true}最小无内容接受信息，不返回历史/备份或内部清理状态；Python内部receipt供维护/测试使用。旧/deletions、/admin/cleanup-jobs及HTTP retry认证后410，修改仍CSRF；前端移除留WP05。

- 实际删除S3以登记bucket/backend/version为界，先核bucket存在，不把NoSuchBucket/403当不存在；版本化Enabled/Suspended或非null版本及不明定位先CONFIG停止；当前最小实现不将单版本HEAD404或delete marker当整个key全清。新上传也先拒绝上述版本化配置，避免新增内容版本副本。未知旧版本/副本需清点及专项批准后另定处置，保留能力，不自动冒进。未完成multipart须在global/生产互斥下按冻结bucket及exact key分页核实、仅abort严格匹配upload并再验证；无权限/不支持/未知不能HEAD404后删ledger或宣告成功，不触其他prefix相邻key。
- SQL内容可先独立清除；每项外部I/O后复核资产和最新job，保存已验证进度。预算20资产/30秒、轮次最短60秒、退避/jitter/S3限流/故障阈值沿§4，不重置失败历史或首次时间；仅已知生产锁忙不加存储失败数。
- app/cleanup_ops.py仅status/health/retry三个维护入口，输出内部ID/固定分类/计数时间版本，不打印路径/key/内容或异常堆栈。维护连接显式独立注入，不复用普通服务凭据；校验实际数据库身份，不能信任环境里自称maintenance。维护表owner为受信边界，不冒称可限制owner直接SQL。retry审计同事务持久化可证明deployment UUID/实际PG role OID/Linux UID（现有reason_code的固定STORAGE_RECOVERED前缀及无内容身份metadata），不只stdout、不声称逐人身份。
- retry需expected_version及固定reason STORAGE_RECOVERED；先全局EX，后来源/task/job锁；安全配置重验后才排队，审计同事务；不提前退避、不重置计数、不解除UNSAFE/PRODUCER_UNKNOWN/LEGACY_APPROVAL，无任意路径/force/mark-complete。
- cleanup_alerts+脱敏日志及runtime原子进度文件提供最低发现。只有global EX的物理执行者写/清独立active-I/O文件，loop扫描进度另文件；其他Worker扫描不能清除或合并覆盖别人的I/O标记。health独立读取loop-completed/active-I/O时间和DB/身份/持久故障；缺失/未知/>60秒不健康，不能看心跳假进展；第5失败、请求>30分钟或危险立即告警，重复信号限5分钟。WP07前须真实巡检渠道/责任人，S3-12只完善。
- 事件90天独立保留，未完成控制不随审计过期删；必要opaque完成定位从completed_at起最多90天，仅用于受控恢复重核。实际应用UUID原片/workspace/DB-result定位可暂留，不含凭据/文件名；任意旧evaluation/BACKUP自由文本或文件名locator在整体验证完成后即删对应asset行/关联，恢复缺定位须门禁+清点。identity可信且intent合法prune后，重核COMPLETED/无未完成资产再清退到期asset/alerts/job/task，不能因老task删新近事件。prune_journal不能把未完成意图直接90天unlink；缺job/缺task为未知也须保留，完成控制须等intent已合法prune后再收缩，不能倒序丢依据。完成事件/控制与受控恢复窗口分别核验。import的CLEANUP_REQUIRED及持续失败同样进入health/固定分类信号，不允许静默永久遗留；自身SH尚未释放导致EX忙，也必须持久失败状态并立即到期等待后续安全扫描，不留原STAGING/STORING静默6小时。

### 7.4 文件所有权与验证停止点

Coding仅：app/cleanup.py、deletion_control.py、cleanup_routes.py、worker.py、processing.py、preprocess.py、media.py、ingest.py、object_store.py；新增app/task_locks.py、cleanup_ops.py及migrations/011_cr_cleanup.sql。queue/tasks/results/support_requests/auth/main、001～010、真实配置/依赖/UI默认只读，确需额外文件先报Main。

Testing仅新增tests/cr_wp04_fixtures.py、test_cr_cleanup_schema.py、test_cr_cleanup_operations.py、test_cr_cleanup_linux.py；必要更新test_cleanup.py、test_deletion_control.py、test_processing.py、test_preprocess.py、test_worker.py、test_worker_queue.py、test_privacy_content.py、test_ingest.py、test_system_failures.py、test_media.py及cr_wp03_fixtures.py的隔离身份兼容（仍严格匹配本包专用PG），不得弱化旧安全目标或并写Coding文件。测试脚本/runner代码归上述测试文件，不修改Docker/compose。

Main独占本设计补充、CR-001_IMPLEMENTATION_PLAN.md、CR-WP04_VALIDATION_REPORT.md。Security只读。专用PG17、私有合成S3、Linux双容器/命名控制卷/FFmpeg测试资源用WP04标签建立/回收，无业务挂载或真实模型。先通过R-03停止证明再认定物理路径安全；失败停止相关实现提替代，不自动验收或启动WP05。
