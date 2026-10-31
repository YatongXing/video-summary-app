# CR-WP01-C：删除、清理与部署者运维设计

**版本：** C0.3提案 · **日期：** 2026-10-30  
**状态：** 本轮仅设计；下列参数、状态和未来命令均待产品负责人审批。命令尚未实现，不可按本文执行。  
**关联：** [共享契约](CR-WP01_CONTRACT.md)、[数据/API](CR-WP01_DATA_API_DESIGN.md)、[切换](CR-WP01_CUTOVER_ROLLBACK.md)。

## 1. 现有证据和变更责任

已读cleanup.py:28～51（独立意图先行、任务栅栏）、112～154（外部I/O持DB锁、MANUAL_REQUIRED停等）、156～186（保留和备份耦合）；deletion_control.py:75～88/106～151（意图/重放/90天）、154～178（旧BACKUP）；queue.assert_fence、processing._workspace/recover_workspaces和preprocess._run。

【已批准】先拒绝/撤销，安全停止/隔离生产，实际清理验证后完成；临时退避限频、持续信号、危险停止、部署者受控审计式再推进，原任务和普通日志30天、删除审计各事件90天。不得沿用“无管理员页面就永久停MANUAL_REQUIRED”的缺口。

## 2. 删除接受与权限边界

1. 当前会话/CSRF/所有权检查；锁序与A/B统一。管理员只可删除自己的视频。
2. 持久化独立无内容删除意图后，事务设置deletion_requested_at、失效lease_generation/排队/后续重试，终止当前support_request并移除可展示凭证，创建/复用唯一cleanup_job和无内容事件。
3. 提交后202仅返operation_id/accepted_at；前端移出列表。独立意图已写但DB提交失败则503、结果未知；意图仍禁止内容/生成/发布，重放补齐，不撤销意图来恢复读取。
4. 支持端所有者/管理员内容检查都读删除栅栏和独立意图；视频逐块复核。任何内容返回、文件创建、对象登记或发布必须继续受当前代次和任务栅栏保护。
5. 按请求ID终止授权，不删除其他任务的新请求；主动删除排队不扣额，活动删除按现有主动取消规则不退已扣额，幂等不重结算。到期删除也终止排障请求。

## 3. 受控生产停止：lease不是物理完成证明

【技术提案】在Linux Docker命名卷中的独立控制目录保存稳定锁文件，不能位于待删除工作区。每个task_id使用一个稳定inode；所有代次生产者持flock共享锁，清理者持独占锁。生产锁覆盖目录创建、写入及子进程存活；Popen明确pass_fds继承同一open-file-description，覆盖FFmpeg/ffprobe及可能写内容的后代。父进程不得在子进程退出前LOCK_UN，仅在全部生产者结束后关闭描述符；父SIGKILL而子仍活时锁仍需生效。实际FFmpeg若不保留所传FD，该方案不通过WP04验证，必须停止相关部分并提出替代，不能回退为只看lease。生产者必须先在DB行锁外取得task SH，再进入fenced_write/heartbeat；清理者先取task EX再进入短DB事务。任何入口不得持DB行锁阻塞等待flock，queue领取事务先结束才进入处理器。目录恢复、模型/媒体生产、finally清理全部遵守该顺序；新建目录仍检查栅栏。锁文件不在运行期unlink/替换，避免不同inode绕开互斥。

清理者尝试同一任务锁的非阻塞独占；拿不到即WAIT_PRODUCER，不删除工作区、不标完成、不增加存储失败次数。获得后复核登记/栅栏，清理并验证。不能以lease_expires_at、Worker心跳丢失、目录暂时为空或进程ID查不到单独判生产停止。

支持前提是所有新Worker遵守协议；WP07先停止旧Worker并确认FFmpeg/旧容器退出后才切换。缺少可验证协议的旧生产者必须隔离/停止，未知则持续信号，绝不假装安全。原recover_workspaces也必须走同一锁/归属/完成规则，不能形成独立无锁删除路径。

锁为本机协作协议，不防恶意root/任意外部写入。目标仅当前单机Linux Docker本地命名卷；不默认为Windows主机/NFS/多主机语义等价。WP04必须双容器验证锁、崩溃释放及inode生命周期，失败则停止相关清理实现，提出替代方案。

## 4. 清理执行、并发与崩溃

候选状态：PENDING、RUNNING、RETRY_WAIT、WAIT_PRODUCER、BLOCKED_CONFIG、BLOCKED_UNSAFE、WAIT_LEGACY_APPROVAL、COMPLETED；除COMPLETED均未完成。任务仍不可访问。状态/时间CHECK确保COMPLETED与completed_at一致；未完成资产为0和生产者已停止由最终锁定事务验证，不能用普通CHECK跨表查询代替。

提案采用短事务领取→数据库锁外I/O→短事务提交，避免当前长网络删除阻塞用户/任务锁：
- 控制器先拿稳定全局维护锁（当前单机全局最多1个），再非阻塞获取稳定任务独占锁，覆盖领取、完整物理I/O及验证；不在DB行锁内阻塞等待文件锁。拿不到任务锁不领取新claim，只记录WAIT_PRODUCER/保留原执行并稍后检查。
- 短事务锁源用户→任务→job，检查到期、删除意图、revision、无其他执行；持久claim_token/claim_time/heartbeat，RUNNING和尝试计数提交。领取自身不授权任意路径。
- 每个资产使用不可变登记ID、所属任务/用户、storage_backend_id/bucket/key/version或工作区定位及生成代次复核；数据库外删除只操作已确认目标。正常可信登记/复合外键/唯一key为归属依据，不要求抵御可任意改DB的root；原片旧key为originals/user/importUUID，不机械要求task前缀。定位字段只由可信登记写入并对普通更新只读，旧登记缺失/矛盾或配置不一致才BLOCKED_UNSAFE，不为回填复制/读取视频正文。
- 逐资产验证后短事务按claim_token/revision提交结果；先前成功不因其他资产失败回滚。SQL正文可独立清除，不能因S3故障保留整份摘要至全任务成功。
- 仅到期claim不允许另一个清理者重叠I/O；后继者必须获得同一任务独占锁，旧执行者持有期间不得抢占；即使其claim过期也不先写token2。前执行token不能再提交成功，但其完成既定幂等I/O并释放后可重新核验。进程崩溃后释放，重新逐项验证，已不存在视为幂等完成。
- 区分online_state和overall_state：COMPLETED仅用于全部已登记、应用可控内容（包括历史BACKUP）已验证清除的overall状态；在线子集完成可标online_state=COMPLETED，但BACKUP待专项批准/未清则overall保持未完成。未知基础设施副本另列UNVERIFIED/隔离且披露，不凭未知自动报无备份。整体完成前锁用户/任务/job，确认claim、生产者停止、全部适用受控资产验证、无未知受控位置，再写overall完成时间及事件；不得沿用排除BACKUP后总成功。
- 运行中数据库不可用则不能登记成功；已完成外部删除在恢复后重验。远端删除在客户端退出后迟到完成，只能针对既定不可复用的已删除任务资产。
- 所有清理入口（定时、到期、导入暂存、工作区恢复、历史BACKUP兼容、部署者再推进）必须明确共用规则或有受审核的独立资产生命周期，不得绕过归属/停止/限频。未完成导入尚无task_id，仍按input_imports可信归属清理，不能用任务前缀猜测。

### 4.1 持久字段/索引与旧状态转换（M2提案）

| 字段/实体 | 类型、约束和更新语义 |
| --- | --- |
| cleanup_jobs.id/task_id/user_id | 保留UUID、task唯一及任务/用户复合RESTRICT FK，first_requested_at由原started_at回填，永不因重试/CLI重置。 |
| overall_state/online_state | overall为本节8状态；online为PENDING/RUNNING/COMPLETED；completed_at只对应overall完成，online_completed_at单列。只有online_state=COMPLETED且剩余阻碍仅为受控BACKUP待批准时，才用WAIT_LEGACY_APPROVAL。online未完时保留legacy_scope_state=AWAITING_APPROVAL，overall仍按PENDING/RETRY_WAIT/WAIT_PRODUCER继续安全在线清理，历史审批不得阻断在线资产。 |
| legacy_scope_state | NONE_REGISTERED/AWAITING_APPROVAL/APPROVED_PENDING/VERIFIED；只描述已登记受控BACKUP，不代表未知基础设施不存在。 |
| claim_token/revision | nullable UUID、bigint>=0；新物理执行产生新token，控制/状态/资产结果变化递增revision，纯活性心跳不递增；旧token/revision不能提交。 |
| claimed_at/claim_heartbeat_at/claim_deadline | timestamptz nullable；RUNNING三者/token必非空，deadline=最近claim心跳+120秒。逾期只是疑似停止，EX锁未释放不能重领。 |
| last_physical_started_at/next_attempt_at | timestamptz；真正新物理轮次开始时更新前者并attempt_count+1，必须>=上次开始+60秒；next按持久退避/预算续轮/生产检查决定。 |
| attempt_count/consecutive_transient_failures | 非负bigint/int；n仅每物理轮含暂时失败时+1一次，全部当前可执行在线资产验证无错/online完成才归0。部分成功、WAIT、预算耗尽、CLI和重启不清n，不推进n。 |
| first_failure_at/last_error_code/last_error_kind | timestamptz和固定枚举码，仅诊断；事件/first_requested_at不随成功重置，持续故障恢复关闭需实际重验。 |
| completed_at/online_completed_at | timestamptz，>=first_requested_at；非完成状态相应时间为空。跨表所有资产=0由最终事务查验，不伪称CHECK可完成。 |
| cleanup_controller_state | 每deployment一行：instance_id UUID、last_loop_started_at、last_loop_completed_at、active_job_id、active_io_started_at、claim heartbeat；非秘密无正文。仅当前全局维护锁拥有者可更新。 |
| ops_command_receipts | PK(deployment_id,request_id)，operator_ref、job_id快照、canonical_body_hash、accepted_at、result/revision；与再调度及审计同事务。请求UUID同体先重放，异体拒绝，不FK已清除job。 |

due索引：(next_attempt_at,first_requested_at,id) WHERE overall_state IN (PENDING,RETRY_WAIT,WAIT_PRODUCER)；持续/blocked索引按state和first_requested_at。RUNNING恢复扫描用claim_deadline，但必须先拿task EX。blocked/待历史批准不自动删除，保持可见信号。实例心跳取真实DB时间；claim心跳线程不冒充清理扫描/进展，health另看last_loop_completed和active_io_started，主循环仍活也能判清理挂起。

旧COMPLETED不能直接信为新overall完成：重新核对受控资产/BACKUP，最多继承有证据的online子状态。旧PENDING/RUNNING重新验claim/生产者协议再调度。旧MANUAL_REQUIRED逐项分类：已证实暂时故障→RETRY_WAIT，配置→BLOCKED_CONFIG，未知/归属矛盾→BLOCKED_UNSAFE，受控BACKUP无批准但online已完成且仅剩该阻碍→WAIT_LEGACY_APPROVAL，online未完仍调度安全在线资产并保留AWAITING_APPROVAL；不批量改COMPLETED，不重置原请求时间、尝试和可核实失败计数。

## 5. 错误分类和重试参数（全部待审批）

| 类别 | 示例 | 处理 |
| --- | --- | --- |
| TRANSIENT | S3连接/5xx/限流、临时文件占用、短时DB不可用、删除后仍可见 | RETRY_WAIT，持久化退避，继续处理已证明安全的其他资产。 |
| CONFIG | 403/凭据错误、挂载缺失、删除控制身份不匹配 | BLOCKED_CONFIG，立即信号；部署者修复后受控再推进，不能当不存在。 |
| UNSAFE | 对象归属不符、未知key/任意路径、逃逸/符号链接异常、不可验证旧生产者/未知版本清理方法 | BLOCKED_UNSAFE，停止涉及资产自动删除，立即信号；retry不得解除。 |
| WAIT_PRODUCER | 生产锁忙，或合法生产者尚未停止 | 只检查停止，不物理删除，不记存储失败；超时持续信号。 |
| ABSENT_VERIFIED | 精确目标确认为不存在，且位置/权限上下文可信 | 该资产幂等成功；NoSuchBucket视配置/存储身份错误，不是目标资产删除成功。 |

| 参数 | 候选值/语义 |
| --- | --- |
| 维护轮询 | 5秒；无任务不产生存储删除请求。 |
| 同一作业首次执行 | 接受后尽快调度；生产者检查不绕过停止证明。 |
| 临时失败退避 | 第n次失败后min(3600,60×2^(n-1))秒；增加0～20%正jitter后再封顶3600；成功完成的资产不重删。 |
| 频率上限 | 同job两次物理清理尝试至少60秒；每主机维护执行全局1个；所有自动/命令共用，不因重启/手动请求重置。 |
| 单轮预算 | 每job最多20资产或30秒，不再启动新I/O；未完成安全资产可续下一轮，不算失败重试。 |
| S3调用预算 | connect 3秒/read 10秒，SDK隐藏重试关闭，失败由持久调度重试；单机删除/验证调用上限10次/秒、burst10。 |
| claim存活 | 心跳15秒，超过120秒标“疑似停止”；必须拿到任务独占锁才恢复，不仅凭超时抢占。 |
| 持续故障 | 第5次连续失败或从请求起超过30分钟未完成任一触发；危险/配置错误立即信号。 |
| 信号/健康 | 控制器心跳每15秒；>60秒无心跳为故障；重复信号最多每5分钟一条，同job/原因仅一开放记录。 |
| 到期/审计整理 | 每60秒扫描；有效性实时判断不等待扫描。删除审计在事件满90天后首次维护轮次删除，延迟可见且不可续期。 |

续轮与失败重试要分开：单轮预算只限制工作量，失败才推进退避；60秒物理尝试下限仍约束续轮，不能靠重置claim或CLI绕过。任务创建30天保留期限、90天审计不是重试预算；超期未清除记录未达要求。

## 6. 持续故障发现渠道

不新增邮件/消息服务，也不重建产品人工后台。提案：
- PostgreSQL持久cleanup_alerts或等效最小信号表：job_id、分类码、首次/最后发生、计数、resolved_at；无正文/路径/访问码。
- Worker输出结构化脱敏stderr，并在获批日志挂载中轮转；不能只有一行“待重试”吞掉持续故障。
- 未来cleanup_ops health检查控制线程心跳、BLOCKED和持续未完成，非零表示需部署者处理；Docker健康检查调用该只读入口，控制线程挂死不能仍仅因DB/S3可连而健康；health是独立调用进程读取持久last_loop_completed/active_io_started，不与被阻塞的扫描线程一起等待。DB不可用返回未知/非零，不能healthy。
- S3-12配置部署者本机状态检查/日志查看及检查频次（候选60秒本机巡检），实际演示故障可被发现。没有运行观察器时不能宣称已自动通知；故障不自动关闭整个API内容权限服务。
- 信号仅实际问题解决/重验后关闭；告警确认、修改配置或命令退出码0不是清理完成。

## 7. 部署者命令规范（未实现，不可执行）

候选接口仅描述未来模块名，不提供立即可用的运维操作：

| 命令签名 | 权限/副作用/输出 |
| --- | --- |
| python -m app.cleanup_ops status --job-id UUID --format json | 本机授权部署者只读；输出作业/任务内部ID、状态、计数、时间、脱敏分类和revision，不输出内容/key/绝对路径。 |
| python -m app.cleanup_ops diagnose --job-id UUID --format json | 只读检查控制身份、claim/生产锁可用性、登记一致性和挂载状态；不修复、不删文件。 |
| python -m app.cleanup_ops health --format json | 只读，检查维护活性/持续故障；适用于Docker/本机巡检，不把连接成功当清理成功。 |
| python -m app.cleanup_ops retry --job-id UUID --expected-revision N --request-id UUID --reason-code STORAGE_RECOVERED | 只持久请求重新调度，不同步删对象；同操作ID幂等，共用限频/归属/安全等待及审计，不能复活任务或延长审计。 |

retry只能由单独、非owner的受限ops角色执行受控调度入口；status/diagnose/health只需脱敏只读视图。ops凭据仅注入部署者一次性工具环境，不挂在普通API/Worker容器，禁止共享旧初始化superuser给运行服务。

retry事务：先按部署实例/请求UUID读取幂等回执，同体返回原已确认结果（先于revision CAS），异体拒绝；再按source用户→task→job锁并核验expected_revision、安全状态/频率；同事务写调度标记、递增revision、操作回执和90天删除操作事件。任何审计/回执失败整体回滚，无I/O先发生。BLOCKED_UNSAFE不能解除；已完成或待历史批准不执行清理；返回0只表示调度已确认。

部署者身份来自受控本机OS/Docker执行权限和部署实例，审计记录deployment_id、OS主体标识、操作UUID/固定原因、目标及结果，不冒用普通应用管理员Cookie。实际可证明的主体是部署实例/ops数据库角色/容器OS UID；任意operator-label不是认证，不能宣称单凭容器exec识别Windows具体人。若需逐人宿主审计，S3-12须另核实受控launcher/OS记录；当前单部署者边界须审批。CLI默认非交互，秘密通过既有受限配置注入，不接受密码/Key命令参数。退出码候选：0检查/调度已确认、2状态需处理/不安全拒绝、3参数错误、4依赖不可用/结果未知、5revision冲突。

禁止--force、--mark-complete、--skip-owner-check、任意--path/--key/--bucket/SQL。BLOCKED_UNSAFE不能被retry清除；登记修复须另提带证据的专项方案和授权。配置问题只在重新核验通过后再调度，重复命令不重置失败次数/首次请求时间。

## 8. 完成验证、审计及历史能力

对象：可信精确key删除后HEAD/必要版本列表验证；版本启用时逐版本策略须核实并获准，普通delete marker不证明全部受控版本消失。工作区：安全根/非跟随链接/生产锁/目录不存在。SQL：双语task_results等正文实际删除；评测：反向关联全部适用对象登记。未知位置保持失败，不能排除后完成。

删除审计包括event_id、deployment_id、必要task/user/operator快照键、事件时间、操作分类、结果、请求/完成时间和脱敏错误码；从每条事件时间起90天，重试产生新事件但不修改旧事件起算。拒绝正文、文件名、URL、密钥、有效码和可恢复内容。

未完成控制另存job/asset最小定位、栅栏、claim、退避、归属/停止证明和最后错误；必要至完成或专项授权解决，即便超过90天也不等于成功。完成后删除不再需要的内容型详情/存储位置，最小事件照期限清除。独立删除记录90天窗口及过旧恢复拒绝继续，未知历史备份的必要控制不随审计到期丢弃。

历史BACKUP登记和独立控制先保留，不把BACKUP_INVENTORY_CONFIRMED改true来解锁成功。在线完成、受控资产整体完成、基础设施历史副本核实分别记录；历史副本先清点隔离核实、专项批准清理、验证完成后再批准退役字段/能力。历史原30天清除义务不自行延长，未完成必须报告；不为恢复/迁移创建新真实内容备份。

## 9. 外部依据（仅核对技术语义，无模型请求）

- flock是建议锁且与open file description相关，必须保证所有生产/清理入口共用稳定inode：[Linux手册](https://man7.org/linux/man-pages/man2/flock.2.html)。
- 清理只证明应用受控资产，不声称介质历史字节擦除；部署者root可绕开应用控制的边界明确保留。
