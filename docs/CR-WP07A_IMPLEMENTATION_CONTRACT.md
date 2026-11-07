# CR-WP07-A 实施冻结与独立验证契约

日期：2026-11-06。用户已选A：当前Windows Docker Desktop Linux容器全新本地部署；本轮只授权G01-G03实现、方案及隔离测试，未授权正式实例创建/开放。真实模型关闭，旧manual4容器/历史10卷及WP06合成实例独立保留，不挂载/启停/标改它们。旧资源仅在本报告列隔离清单，不通过Docker label写修改旧容器。已验收前端src/dist、001..011 SQL、历史证据全部冻结。

## G01接口与兼容
- 新增app.maintenance，提供ensure_open()（不开放则JournalError）、ready()及read_state()；MAINTENANCE_GATE_REQUIRED=true时强制门禁。
- 状态文件位于DELETION_JOURNAL_DIR/runtime/maintenance.json，精确字段deployment_id与state(OPEN/CLOSED)。匹配独立journal及DB身份，路径/文件失信、缺失/损坏/未知state一律拒业务；读取不能创建缺失目录或重建身份。使用既有fsync原子写。
- 维护CLI python -m app.maintenance status|close|open；close/open只使用MAINTENANCE_DATABASE_URL，验证当前DB身份为tasks的维护owner，不允许runtime操作。set_state(state,connection=None)可供受信bootstrap传真实owner连接；不得普通API/请求头/ADMIN旁路。open必须从可信既有状态转换；close可在可信已初始化全新控制中建初始CLOSED状态。
- 为不影响现有只读绑定源码的WP06/历史测试，旧环境未设置MAINTENANCE_GATE_REQUIRED时保持既有行为；新构建Docker运行镜像ENV及WP07模板强制true，bootstrap/preflight拒绝false/缺失配置；不得据兼容模式部署新实例。
- 覆盖API入口/当前用户/业务读写及ready、流read前后、Worker启动与循环/领取/心跳/发布、下载/转码/上传和物理清理I/O边界。置位后停止后续业务，不仅检查请求入口。恢复OPEN后可恢复队列；关闭期间不将维护误记成功/退款或越权发布。离线owner维护重放仍经维护凭据，可更新控制但无HTTP旁路。

## G02新部署与G03巡检
- 新资源仅专用测试prefix vsa-wp07a-test-*、label vsa.work_package=CR-WP07-A 和vsa.synthetic_only=true；首次运行须检查无冲突，只回收本次资源。正式未来另名vsa-local，绝不本轮创建。
- 三数据库身份：一次性初始化基础设施管理员、非superuser schema/database owner维护、非owner非superuser/NOCREATEDB/NOCREATEROLE/NOBYPASSRLS runtime；无runtime继承/SET ROLE维护。运行服务只持runtime DSN及私有桶必要凭据。初始化与维护秘密只在专用一次性服务/受控通道。
- init仅新DB/全新独立卷，执行原001..011；维护可重连、角色ACL检查、journal身份、初始CLOSED和新部署producer-ready。不存在旧授权/旧服务不制造终止动作。
- runtime历史consent只有清退定位列SELECT及DELETE；support只给legacy关闭清退所需SELECT(task_id,user_id,closed_at)、UPDATE(closed_at,close_reason)、DELETE，不许INSERT/全文授权读取。需要触发器/任务版本、审计、正常删除实测。
- 非root应用固定UID10001；初始化卷权限可专用一次性root/chown，业务进程始终非root；Linux named volumes承载journal/locks/staging/work，APP不能访问Docker socket。所有发布端口绑定127.0.0.1；DB/S3不对外发布。新web容器只服务已批准dist并同源代理/api，不更改前端页面。
- G03 host巡检工具只针对state中已验证新测试资源及localhost；可--once/短周期(正式默认<=60s)，检测API/Worker/DB/S3/cleanup和持久故障及恢复。持久状态/事件不含正文/密钥/SQL原文。通知仅测试本地file receiver，验证故障/持续故障/恢复确实送达；正式渠道/接收人待用户确认，不用私人账户/外部邮件。不得安装真实计划任务；可交Windows运行模板、在测试中启动隐藏临时观察器后回收。

## 文件白名单与所有权
- Main：docs/CR-WP07A_*及docs/baseline/cr-wp07a-*.json；冻结/报告/方案/审批清单。除新资料不写其他文件。
- G01 Coding：新增backend/app/maintenance.py；必要改main.py/auth.py/content_auth.py/deletion_control.py/worker.py/queue.py/tasks.py/results.py/ingest.py/media.py/preprocess.py/processing.py/pipeline.py/object_store.py/safe_download.py/cleanup.py/task_locks.py/cleanup_ops.py。仅门禁检查及必要暂停恢复，不重写业务规则/SQL/前端。不改测试。
- G02/G03 Coding：backend/Dockerfile；新增backend/app/deployment.py；deploy/compose.wp07.yaml、wp07.env.example、Dockerfile.web、nginx.wp07.conf（或等价仅一个静态web代理实现）；新增backend/tools/wp07_monitor.py及Windows .ps1运行模板；部署新文件必要.gitignore/dockerignore仅保护secret/build上下文。不得改现有compose/真实.env/前端/SQL/测试。实现选择如需新具体文件先Main说明记录后创建。
- Independent Testing：新增backend/tests/test_wp07a*.py、必要新fixture、backend/tools/wp07a_environment.py/验证runner及frontend/tests/wp07a-real.mjs；必要既有tests只作环境模式适配不能降低断言；docs/CR-WP07A_TESTING_REPORT.md及baseline/cr-wp07a-testing.json。拥有测试/资源生命周期，不写业务。可调用新部署脚本完成新鲜隔离创建，但不能创正式实例。新前端只复用构建产物不可build改dist。
- Independent Security：源码稳定后只读审G01/G02/G03、非root/秘密/门禁旁路和旧资源隔离；报告Main不并写。

## 执行与停止
Main冻结→G01/G02G03编码并行、Testing独立准备→定点诊断→独立Security→正式隔离验证/必要完整非模型回归→Main核SHA和旧资源未改→交付后停止。测试覆盖全部用户清单，真实HTTP/Worker/PG/RustFS、非root、Linux锁/父死子活/FD、持久门禁跨重启、队列/发布/流/I/O拒绝和恢复、三身份/维护重连/ACL、实际故障通知file receiver与恢复；模型Fake，原用户页面及Task ID语义不变。

每层报告真实执行/失败/未执行，不相加重叠数量；不以旧312/58结果冒充本次。正式私有/公网开放均未授权，隔离服务临时loopback端口只用于本轮合成验证。完成后回收本轮测试资源，保留无内容证据；没有要求保留新人工环境，不擅保留正式服务。

## 集成冻结补充
- G03真实Worker进度复用worker.py，strict部署写runtime/worker-<HOSTNAME>.json约5秒，health要求<=60秒；只读探针不能重建缺失控制。既有legacy未设flag不要求此新文件。
- 根.dockerignore仅白名单已批准frontend/dist、新web recipe及deploy/wp07_web.py；后者为旧WP06已验证stdlib流式代理的等价实现。nginx镜像匿名令牌请求超时，采用本机缓存Python镜像，不改变前端。未使用的新nginx配置移除，历史文件不动。
- Testing既有WP02/03/04 fixture及Linuxdriver可新增显式WP07A prefix/端口/标签验证分支，旧guards原样；新回归库仍须在本轮专用PG内，禁止伪造旧WP04身份。环境适配不降低物理锁/FD/事务断言。
- 门禁中已发生I/O的资源关闭、终止自有子进程和清退定位等安全补偿允许；普通业务事务须末端复核并回滚，不能仅在HTTP响应时拒绝而让发布/扣退/删除状态提交。受信record=False离线控制重放独立保留。
