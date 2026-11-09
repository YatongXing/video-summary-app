# CR-WP04：删除、清理与受控维护验证报告

**日期：** 2026-11-05  
**依据：** 用户已正式验收WP03并独立授权WP04；已验收WP01 L1.0、L-01～L-05/R-01～R-07，以及[实施冻结](../../CR-WP01_DELETION_OPERATIONS.md#7-wp04实施冻结2026-11-05技术落实)。  
**状态：** 实现、独立测试、安全复核及资源回收完成，提交产品负责人验收；**未自动认定已验收，未启动WP05**。  
**Git：** implementation，HEAD `96b093afed1ef0cb36685ba46325d52c805d612c`，承接原未提交内容，不执行commit/push/stash/reset/clean/checkout。

## 1. 实际文件与实施范围

| 责任 | 文件 | 作用 |
| --- | --- | --- |
| Coding | [cleanup.py](../../../backend/app/cleanup.py)、[deletion_control.py](../../../backend/app/deletion_control.py) | 四态、短事务验证、重试/预算/故障发现、意图重放、审计/控制收缩及受控历史兼容。 |
| Coding | [task_locks.py](../../../backend/app/task_locks.py)、[cleanup_ops.py](../../../backend/app/cleanup_ops.py)，新增 | Linux稳定SH/EX/anchor与status/health/retry维护入口。 |
| Coding | [worker.py](../../../backend/app/worker.py)、[processing.py](../../../backend/app/processing.py)、[preprocess.py](../../../backend/app/preprocess.py)、[media.py](../../../backend/app/media.py) | 生产生命周期锁、子进程FD继承、取消kill/wait、独占工作区及恢复互斥。 |
| Coding | [ingest.py](../../../backend/app/ingest.py)、[object_store.py](../../../backend/app/object_store.py) | 导入SH/EX与失败持久化、冻结存储身份、版本化停止、短超时与multipart核实。 |
| Coding | [cleanup_routes.py](../../../backend/app/cleanup_routes.py) | 删除202最小接受响应；旧删除记录/人工重试接口认证后410。 |
| Coding | [011_cr_cleanup.sql](../../../backend/app/migrations/011_cr_cleanup.sql)，新增 | 原位四态/单job_version、资产及导入不可变定位、维护批准限制、旧状态/次数保守映射。只在专用合成实例测试，未迁真实库。 |
| Testing | [cr_wp04_fixtures.py](../../../backend/tests/cr_wp04_fixtures.py)、[test_cr_cleanup_schema.py](../../../backend/tests/test_cr_cleanup_schema.py)、[test_cr_cleanup_operations.py](../../../backend/tests/test_cr_cleanup_operations.py)、[test_cr_cleanup_linux.py](../../../backend/tests/test_cr_cleanup_linux.py)，新增 | 独立PG/S3/故障/维护/实际Linux进程与双容器证明。 |
| Testing | [test_cleanup.py](../../../backend/tests/test_cleanup.py)、[test_deletion_control.py](../../../backend/tests/test_deletion_control.py)、[test_ingest.py](../../../backend/tests/test_ingest.py)、[test_media.py](../../../backend/tests/test_media.py)、[test_processing.py](../../../backend/tests/test_processing.py)、[cr_wp03_fixtures.py](../../../backend/tests/cr_wp03_fixtures.py) | 保留原隔离/正文删除/物理oracle/错误可恢复目标；适配四态、410、生产ready、冻结locator、受控Popen及严格WP04测试身份。 |
| Main | 本报告、[计划](CR-001_IMPLEMENTATION_PLAN.md)、[删除设计](../../CR-WP01_DELETION_OPERATIONS.md)§7 | 启动/字段/文件责任冻结、缺陷派发与交付整合。 |

只实现已批准WP04。未改前端、真实配置/依赖、001～010、auth/results/support_requests/main或queue/tasks；旧S3-10报告与原始JSON保留，不把历史结果追记为新需求通过。

## 2. 删除与数据契约

- 身份/CSRF/归属仍由现有认证入口检查，管理员只能删除自己的任务。源用户→task锁序；独立无内容intent先fsync，同事务删除栅栏/lease失效、取消后续调度、关闭请求/推进support_version、创建或复用job和事件。
- DELETE /tasks/{task_id} 始终202表示接受，响应仅 `{id,task_id,accepted:true}`。这不等于物理完成。旧 /deletions、/admin/cleanup-jobs 及HTTP retry认证后410，修改仍CSRF；前端入口移除属于WP05。
- SQL正文、摘要/导出及普通日志可在短事务先清，不依赖物理生产停止。BLOCKED及刚到期的新PENDING同样执行保留期SQL清除，不因对象危险或首次due时间差保留正文。
- job仅PENDING/RETRY_WAIT/BLOCKED/COMPLETED。保留原started_at、总attempt_count、consecutive_failures、last_physical_started_at/next_attempt_at、online_completed_at/overall completed_at与单job_version；取消旧RUNNING/人工标志/worker_safe_after/备份状态字段，没有claim/回执/清理影子。
- 旧RUNNING→BLOCKED/PRODUCER_UNKNOWN；旧MANUAL按已知暂时/危险/纯时限分类接管；旧COMPLETED回PENDING重核。旧失败n保守承接attempt_count，不重置原时间/已知次数，不盲信旧资产DELETED。
- media_assets及input_imports冻结task/user/key与存储身份。新S3 backend绑定规范endpoint摘要，workspace/staging绑定安全根身份；旧未知位置保留CONFIG，不用当前环境猜测。source、key、bucket、backend或归属错误均不删，DB结果UUID也核任务归属。
- 每项外部幂等删除后实际核验，再短锁重读当前job/version及资产。陈旧I/O不能覆盖新阻塞/调度；SQL断连不记成功。完成必须受控应用资产真实清除，无未完BACKUP；online完成单独记录。
- 无主动内容备份。BACKUP仍有受控兼容：仅维护owner逐资产批准，普通INSERT/UPDATE不能伪造批准，backups/只对已批BACKUP有效。未批历史不删，安全在线清理继续；不把未知基础设施副本标不存在。
- 已知版本化Enabled/Suspended、非null版本、缺桶/403/未知后端停止；不把delete marker或单版本HEAD404当整个key清完。新上传也拒绝产生版本副本。历史版本化处置先清点专项批准。
- 对精确bucket+key分页核实未完成multipart，仅abort严格相同key的UploadId并重新列举验证；Prefix相邻对象不操作，能力/权限未知不能删ledger并称完成。

## 3. 生产停止、并发与故障处理

生产SH覆盖整个processor/导入生命周期，领取SQL事务结束后才等锁；所有FFmpeg/ffprobe走受控Popen并继承当前作用域FD，正常取消kill/wait。父只close，不LOCK_UN，子仍活时保留同一OFD锁。控制卷locks不属于待删工作目录；hardlink anchor/fstat/lstat检测inode替换，拒绝链接逃逸，不运行时unlink/重建锁。

物理顺序：全局清理EX→task/import EX→来源/task/job短SQL。锁覆盖外部I/O，不持SQL行锁跨网络；DB失联、lease或超时不能让后继者接管。正常finally仅清本次已停子进程的独占工作区；恢复/整任务清理共用EX。物理并发1不限制视频处理N；全局可配置N、普通用户1、FIFO/配额/退款/重试及原性能目标保持。

producer-ready是绑定deployment_id的离线排空证据，程序不自动生成。缺失或失信则PRODUCER_UNKNOWN、不物理清理；新EX空闲不能证明旧无锁生产者已停止。真实部署只能在WP07确认旧服务/FFmpeg后代及I/O排空后建立，不能把此文件当完整业务恢复门禁。

| 参数 | 本包语义 |
| --- | --- |
| 轮询 | 5秒，首次尽快但不绕过安全；每轮先非force重放intent，SQL失败无需等重启补齐。 |
| 失败退避 | min(3600,60×2^(n-1))秒，0～20%正jitter后3600封顶；每轮最多增一次n。 |
| 物理频率/预算 | 同job至少60秒；20资产/30秒，逐次S3调用包括节流后检查，预算续轮不加n/不归零。 |
| S3 | connect3/read10秒、无隐藏重试、跨进程共享节流不超过10调用/秒；成功验证资产不重复删除。 |
| 发现 | 第5连续失败或请求>30分钟，危险/配置立即持久告警；DB/控制/日志/I-O未知不报告健康。 |
| 进度 | loop与active-I/O独立文件，仅global owner写/清I-O；导入/工作区回收也纳入，其他扫描者不能遮盖挂起。 |
| 重复信号 | 持久同类告警幂等；维护异常首次立即，后续至少300秒一次。 |

删除意图已持久而SQL提交失败时继续拒读/发布；后续run_once非force重放补job，不撤无关会话/授权。导入自身SH尚未释放导致清理EX忙，也持久CLEANUP_REQUIRED及即时到期，后续安全扫描推进，不留六小时静默失败。

## 4. status/health/retry 与最小审计

```text
python -m app.cleanup_ops status --job-id UUID [--details]
python -m app.cleanup_ops health
python -m app.cleanup_ops retry --job-id UUID --expected-version N --reason STORAGE_RECOVERED
```

status/health提供内部ID、固定分类、版本/计数/时间及依赖未知，不输出内容、key、路径或凭据。故障索引保存在cleanup_alerts，部署者通过受控维护通道只读定位job ID后使用status/retry；不向普通管理员Cookie开放。health独立读取真实loop/I-O进度，缺失/读错/>60秒无进展或持续故障均非健康；没有已部署观察器就不声称自动通知。

retry仅显式独立MAINTENANCE_DATABASE_URL，服务不注入；实际PG表owner验证，不信环境自称角色。globalEX内显式事务覆盖autocommit根事务或既有外层savepoint，版本检查、重排与审计一致；不提前next、不重置首次时间/n、不解除UNSAFE/PRODUCER_UNKNOWN/LEGACY_APPROVAL，无force/mark-complete/任意path/key/bucket/SQL。

审计同事务记录可证明deployment UUID、PG角色OID、Linux UID，属于实例/数据库身份，**不是逐人操作员认证**；维护owner为受信部署者，不声称工具限制其直接SQL。退出码0确认、2安全拒绝/需处理、3参数、4依赖未知、5版本冲突。CLI成功仅检查/调度，不表示清完。

每事件90天由created_at独立起算，老task不能带走新审计。未完成job/必要定位/intent保留，不因90天删掉或假完成。验证完成即去掉任意自由文本/文件名定位，必要opaque UUID定位最多保留completed_at起90天供受控重核；身份可信、intent合法prune、重锁核版本/完成后才清退旧控制。缺task/job是未知，不能当完成。恢复缺必要定位时保持门禁并清点，不新增恢复体系。

## 5. 独立测试与实际证据

最终共 **339个不同用例通过**：**333个后端用例（292既有 + 41个WP04新增core） + 6种真实Linux物理配置**。不累计诊断或重复复测；最终12份源码运行前后SHA一致。

| 最终执行 | 结果 | 证据 |
| --- | --- | --- |
| fresh PG完整后端 | **333 passed / 0 failed / 0 skipped，148.58秒，exit 0** | Linux test镜像加载当前只读源码；41新增core为16结构+25操作。 |
| Linux物理主组 | **5 passed / 31.63秒** | FFmpeg孤儿/SH、多任务并发、全局EX/inode、双Worker删A不伤B、DB断连仍不接管。 |
| ffprobe补充配置 | **1 passed / 6.68秒** | 真实ffprobe父死子活、FD继承和EX边界，与前五种配置不同。 |

旧规则/fixture曾因废止字段、410、ready/冻结定位或受控Popen不适配；保留原安全目标后定点更新，实际源码缺陷按§6修复。最终前一轮323通过/10 setup errors来自可销毁PG的public保留早期候选011 checksum，迁移器正确拒绝；没有改写登记，而是核本包标签后销毁重建空白专用PG，再按同一最终源码完整通过。所有诊断结果不计最终通过数。

真实PG/S3成功与隔离oracle已经运行，异常和时间边界同时使用受控注入；不能把它们称为真实网络黑洞、宿主断电或所有卷驱动的证明。

| 场景 | 证据类型与oracle |
| --- | --- |
| 011约束/旧状态/失败计数、FK/唯一/不可变/owner批准 | 专用真实PG，旧合成001～010升级、重放/事务及旧SHA；不碰真实库。 |
| 请求删除/SQL清正文/权限/410/202/授权同步 | HTTP/ASGI+PG及独立事务测试；物理完成另查对象/文件，不能以404当清完。 |
| 暂时/配置/危险故障、重试/预算/陈旧版本 | 真实PG与受控故障/时间接缝；区分注入存储失败和真实S3成功，不称真实断网全链已覆盖。 |
| 真实S3清理与multipart | 专用私有RustFS、合成对象/未完成upload，逐目标HEAD/list验证及其他key保持；非业务桶。 |
| 父死子活/共享锁/inode | 真实Linux双容器/命名卷，父SIGKILL后实际媒体文件继续增长，EX仍被拒，子停才可取得。 |
| N>=2/删A不伤B | 两真实Worker/PG配额及FFmpeg同时生产，A取消后工作目录实消失且job完成；B仍PROCESSING、文件继续增长。 |
| DB断连不接管I-O | 实际pg_terminate_backend后文件继续追加，另一容器globalEX仍忙，原物理I-O结束后才可取得。不是远程S3黑洞等同证明。 |
| 维护审计提交失败/不自动批准 | 真PG deferred COMMIT故障、autocommit/已有事务、权限/版本及拒绝参数；状态/版本/计数/退避不被部分提交。 |
| intent提交失败自动恢复/保留期 | fsync成功SQL回滚，撤故障run_once无需重启补齐；BLOCKED/首次到期SQL不残留，90天事件与未完成控制分离。 |

原S3-10报告/JSON未覆盖。新非模型回归不代表真实Qwen、质量、原P90新容量基线或WP07切换通过。

## 6. 独立安全复核与修复

/root/wp04_security只读审设计/源码，未写实现或操作数据库；运行证明由独立Testing承担。Main逐项派Coding修复，再由Security定点复核、Testing保留反例。

| 编号 | 风险与关闭方式 |
| --- | --- |
| W04-S01 | BLOCKED+NULL被PG CHECK三值逻辑放行；加显式非空约束。 |
| W04-S02 | 导入用当前配置猜旧定位/上传后漂移错登记；冻结input ledger，上传前后完整核验、asset采用ledger定位。 |
| W04-S03 | 其他扫描者覆盖active-I/O、导入/恢复漏标记；分owner-only I-O与loop文件，全部物理入口共用。 |
| W04-S04 | 完成后长期保存自由文本locator；即时去自由文本、opaque控制完成90天收缩、未完成例外保留。 |
| W04-S05 | BLOCKED或首次到期PENDING永留SQL正文；独立按复合归属短锁清除，不触物理资产。 |
| W04-S06 | 迁移旧失败n归零；按attempt_count保守承接且原时间/next不倒退。 |
| M04-ATOMIC | 维护重排依赖隐式事务；显式global内事务覆盖CAS/证据审计/提交失败。 |
| M04-REPLAY | intent提交失败仅靠启动重放；每轮非force重放，无关会话不撤，失败不刷新完成进度。 |
| 导入忙/MPU/预算 | 锁忙失败仍持久、精确multipart验证、每调用deadline；不以删断言或“HEAD404”消除问题。 |

**独立静态结论：未发现仍需阻塞WP04交付的问题；S01～S06及原子性/重放修复已定点复核关闭。** 最后复核cleanup.py为 `276cb9ce76b83db632b30eedb0231c282d45eeaf3bcef396ba3b1dd80e827aa9`，worker.py为 `113ce4370d33088260bdcffe10cb2b278527debf86926d334f6c9bafb3053391`，011为 `b69ddc558587b7c8221ecbac862592512d78cae11b0a40a2b5d4835cd393a722`。这不是产品验收，也不代替独立运行证据。

## 7. Agent 派发与交接

| 子任务 | 输入/所有权/交接 |
| --- | --- |
| Main /root | 已批准L1.0/R-03/R-04，118份起点指纹，§7冻结字段/状态/接口/锁序/文件责任；统一计划/报告与缺陷分派，不常规业务编码。 |
| WP04-Coding /root/wp04_coding | §7、AC及现有源码；只9份旧源码+2新模块+011，不写测试/docs/配置；交自检/SHA后接受Main定点修复。 |
| WP04-Testing /root/wp04_testing | 独立读AC/E2E/CR测试计划§4/7与冻结契约；独占四新测试及必要旧适配，建立专用资源、运行/故障/物理oracle与复测；不改Coding文件。test_media最小spy适配经Main显式补白名单。 |
| WP04-Security /root/wp04_security | 定向只读生产/删除/授权/维护/恢复边界，直接报Main，不并写源码；修复后定点复核，不以静态审查代运行证明。 |

每次派发均有WP/子任务ID、输入版本、模块/可写路径、冻结契约、必做/禁做、AC/T、证据、依赖/交接九项；共享契约变化由Main同步，非各Agent自行变更。编码、测试、安全完成均不代表负责人验收。

## 8. 隔离资源与复现

PG `vsa-wp04-pg-test`，17系列、tmpfs、wp04_test、随机回环57055；S3 `vsa-wp04-s3-test`，私有RustFS/tmpfs、随机回环57674；均CR-WP04/synthetic_only标签，合成凭据/媒体，无业务数据卷。已有Linux test镜像只读挂当前app/tests/tools，外部模型关闭，未读取真实.env/Key/Cookie或用户视频。

测试Agent已回收Linux证明容器/命名卷和runner；HTTP子进程由夹具finally终止。Main接续额度中断后的资源终检：PG实际17.11，非系统/非public schema为0、其他连接为0；两服务的CR-WP04与synthetic_only=true标签均匹配，PG数据为tmpfs，S3的/data为tmpfs，两者auto-remove。

Main只停止 `vsa-wp04-pg-test` 和 `vsa-wp04-s3-test`。停止成功后按标签/名称复查：WP04容器、命名卷和网络均为空。没有全局prune、没有停止业务容器或删除业务卷。未额外建立WP04网络，仅使用默认bridge及专用PG网络命名空间。

早先宿主pytest诊断目录未做全局删除，保留资料全部为合成数据；不声称清除了宿主所有临时资料。

复现需同类专用资源、显式TEST_DATABASE_URL及WP04身份/端口和S3合成环境。旧WP02三文件仍要求原独立身份，未伪造条件混跑；WP03 fixture仅扩展严格匹配WP04专用身份，没有回退业务库。

后端实际执行于已有 `video-summary-app-test:latest`（镜像ID `220689d0be48`），只读挂app/tests/tools，工作目录/srv，显式关闭模型调用；网络共享本包PG命名空间，容器内TEST_DATABASE_URL指向127.0.0.1:5432/wp04_test，WP04_TEST_PORT=5432；宿主驱动物理证明使用57055。没有传入真实.env。

```text
python -m pytest tests -q -rs --tb=short
  --ignore=tests/test_cr_support_schema.py
  --ignore=tests/test_cr_migrations.py
  --ignore=tests/test_cr_roles.py
  --ignore=tests/test_cr_cleanup_linux.py
```

以上分行仅展示同一命令的参数。物理文件由宿主pytest以WP04_LINUX_DRIVER=true及显式专用PG身份驱动Docker；最终主组5项与后补ffprobe1项分开执行，不与后端333项重复计数。完整重跑该物理文件的入口：

```powershell
& backend\.venv\Scripts\python.exe -m pytest backend/tests/test_cr_cleanup_linux.py -q --tb=short
```

执行前必须建立同类可销毁资源并注入对应测试身份/端口；原实例已回收，不能直接沿用旧端口连接其他服务。

## 9. 完整性与兼容

以WP04开始时118个可见文件指纹为基线：17份修改、101份逐字节不变，新增8份，共25个本包交付文件；无基线文件丢失或白名单外修改。§1为实际清单，未使用全部预先授权路径。

001～010逐字节不变，旧迁移器/校验未改写；011最终SHA为 `b69ddc558587b7c8221ecbac862592512d78cae11b0a40a2b5d4835cd393a722`。原S3-10报告/JSON、WP02/WP03报告、前端/依赖/配置模板及queue/tasks/auth/results/support_requests/main均保持WP04起点。未读取真实.env。

| 实现/测试资产 | SHA-256 |
| --- | --- |
| backend/app/cleanup.py | `276cb9ce76b83db632b30eedb0231c282d45eeaf3bcef396ba3b1dd80e827aa9` |
| backend/app/cleanup_ops.py | `0392984cd0e9d8ff619594ea3246159d5a49d098133eec61dea31715a69e2dfb` |
| backend/app/cleanup_routes.py | `cfb03d7ad5c92b78d328e9eb771b54d5b86e9917048ba9df6dd869825c04af53` |
| backend/app/deletion_control.py | `81ff5987d0c31344c586930450bde067c55b09cdda3c41be02843d6b606bb21b` |
| backend/app/ingest.py | `701587ceb2242bfe573128fe86be71766c06bd3d40d0312e6ce3cba82b670b61` |
| backend/app/media.py | `582da9021ec583ea24e94e6b2b256f24a21527f31d86c6ea691884451bd20992` |
| backend/app/migrations/011_cr_cleanup.sql | `b69ddc558587b7c8221ecbac862592512d78cae11b0a40a2b5d4835cd393a722` |
| backend/app/object_store.py | `580a50de1c7a899f0112de980a4e51de5b6e3889472a425c11fd12cc5cbb6e72` |
| backend/app/preprocess.py | `abfc97373366d952ffe15ba78096fb5b15748f8cf6e082b7523f2bf3babb356f` |
| backend/app/processing.py | `39462de4033da7f846e4dfb7e92ac7aeac79adb3a087431810cec789d3d19734` |
| backend/app/task_locks.py | `3a43ae46d418dea7b5060b73f9bc78c7c35146c43a2804593cf95b913f714410` |
| backend/app/worker.py | `113ce4370d33088260bdcffe10cb2b278527debf86926d334f6c9bafb3053391` |
| backend/tests/cr_wp03_fixtures.py | `04e7ea7f9a4d7782835dff383321508f161caecb639ebc26547f461119713293` |
| backend/tests/cr_wp04_fixtures.py | `536bfb338b80cedf3be46b34184c424f9e1cbf0d0840e917ca44f977d83c9937` |
| backend/tests/test_cleanup.py | `37a00c69f3c1dcec35ca73b772b043172bbf4e9ad9c2b7a51203dabeaa537313` |
| backend/tests/test_cr_cleanup_linux.py | `804b73a6325057d07ebd4605ff3d799114304e892b3b975206510347c53cb05e` |
| backend/tests/test_cr_cleanup_operations.py | `397770097613d107d15eee56b284f417b0ebe0d0ad23f3ab323846fe922116cc` |
| backend/tests/test_cr_cleanup_schema.py | `355e9cef050eec88dfd88be26be0b30f666474c297d47b06108b2dccc1ab1020` |
| backend/tests/test_deletion_control.py | `b1e2ed4b08fb02d6d1dddd6f9302e0522a4e5553988c285388f376a4380950d1` |
| backend/tests/test_ingest.py | `1fbe3280429d0a87b8c38be5ee84d83cef537519112f2fba239efa4b7ed48a96` |
| backend/tests/test_media.py | `3c7519dd62d9902c1673ef2915967c0135d78372931b639737c88975c426672e` |
| backend/tests/test_processing.py | `9d893caaf9c7e771a4688dacac0be0c525c2101b222ba538cc5c1b1c2db73446` |

最终终检：38个本地Markdown链接及适用锚点全部通过，新增文件空白/EOF检查通过，git diff --check exit 0（仅LF→CRLF提示）。12份源码与独立测试/安全复核指纹完全一致；118份起点文件中101份未变，17份修改及8份新增全部在授权范围，无意外删除。

原有未提交内容/历史删除原位保留。Git仅建议负责人另授后按本报告白名单审阅和暂存，排除.env、密钥、内容资产、卷、日志和缓存；本轮未commit/push，也未创建含用户内容备份。

011是原位精简，**不兼容S3-10旧binary的旧清理字段/状态**；不能边跑旧服务边在真实库应用。已批准WP07先关闭入口/维护门禁并排空旧服务、后代、I-O/连接，再迁结构；只向前修复或回当前协议兼容镜像，不down-migrate复活purpose。当前只在可销毁合成schema迭代验证，最终交付011指纹冻结，未改任何真实已应用checksum。

旧未知locator需WP07另授离线清点/维护owner受控重登记及审计；普通retry不能修复或猜测所有CONFIG，不新增第四条维护命令。历史备份未获批准前保持未完成，不因清理UI/API退出遗留无人负责。

## 10. 剩余边界、退出与WP05准入

**Main结论：实现、独立回归及定向安全审查完成，未发现未解决的本包业务失败或安全阻塞，建议产品负责人验收WP04。尚未自动认定已验收，WP05未启动。** 结论限于本报告配置与证据；未知历史资产和实际部署前的专项条件仍须落实。

- 原生Windows不提供flock等效降级；物理生产/清理的支持目标为已验证Linux Docker本地共享卷。不同存储/挂载实现不能自动沿用证明。
- 版本化旧桶/不明位置/未知旧生产者/未批BACKUP保持阻塞，需有责任的离线核验及专项授权；未知历史副本没有被本包删除。
- 已发网络字节/下载副本/模型供应商/历史介质字节不在即时撤回保证内；不承诺物理介质安全擦除或无内容备份下可恢复数据。
- health/status/持久信号已经交付，完整扫描挂起/日志/DB/网络故障闭环仍须WP06；WP07开放前必须部署实际巡检/责任人，不能把CLI存在说成监控已上线。
- WP07完整maintenance.required业务门禁、旧凭据/连接失权、旧授权终止及恢复开放未在真实环境执行；producer-ready不替代这些要求。
- WP05必须单独授权并在WP03/04验收、数据/API稳定后开始：管理员owner直读、UUID三scope/24h/409→GET、列表/定位/撤销/再生；移除删除记录/人工页及旧purpose请求，沿用202仅接受语义，不从页面消失推断物理完成。独立浏览器/前端测试由Testing承担，后端/物理能力不能被UI削弱。
- WP06/07、S3-11/12及Git保存另行授权。本包完成后停止，等待负责人正式验收。
