# CR-WP01-A/B：数据、访问码、API与权限契约

**版本：** AB0.3 · **日期：** 2026-10-30  
**状态：** 主Agent整合S1设计、S2测试与S3安全发现的技术提案；全部具体技术选择/参数待产品审批，未创建或运行SQL/业务实现。  
**协调基准：** [C0.3共享契约](CR-WP01_CONTRACT.md)；已批准产品规则见[CR-001](CR-001.md)/[ADR-005](adr/ADR-005-cr-001-deletion-troubleshooting.md)。

## 1. 实体、字段与约束

support_requests是请求及同一次授权的唯一权威。request_id每次重新生成新建，任何旧ID不按task_id追随新授权。

| 表/字段 | 类型/约束及职责 |
| --- | --- |
| support_requests.id | UUID PK，对外request_id。 |
| task_id,user_id | UUID NOT NULL，复合FK到tasks(id,user_id)，RESTRICT，无删除源task的级联副作用。 |
| scope | summary/video/both枚举；不可就地变更，范围变更必须重新生成。 |
| created_at,expires_at | timestamptz；expires_at严格等于created_at+86400秒，不用日历1 day；不可更新。 |
| auth_epoch | UUID，必须匹配当前独立部署控制及DB协议状态。 |
| state | ACTIVE/REVOKED/REPLACED/DISMISSED/TASK_DELETED/EXPIRED；终态不可回ACTIVE。 |
| closed_at,closed_by_user_id | ACTIVE时NULL，终态时间>=created_at，操作者可空，仅服务端填写。 |
| code_hash | bytea32 NOT NULL，全表唯一，摘要不是任何API可用凭证。 |
| code_ciphertext,crypto_key_id,nonce | ACTIVE时必须非空，nonce12字节；终态全部清空，不保留可展示旧码。 |
| generation receipt | support_generation_receipts(task_id,user_id,request_key UUID,canonical_body_hash bytea32,result_request_id UUID snapshot,created_at)；PK(task_id,request_key)，只FK任务/用户，不FK请求。 |
| crypto key registry | support_crypto_keys(key_identity UUID PK,fingerprint bytea32 UNIQUE,state WRITING/DECRYPT_ONLY/RETIRED,created_at,nonce_count bigint>=0,retired_at)；无原key/用户/内容；保留已退役fingerprint/身份配置以拒绝同真key换ID复用，不按删除审计90天清除。 |
| nonce ledger | support_nonce_reservations(key_identity,nonce bytea12,reserved_at)；PK(key_identity,nonce)，无任务/用户/内容，无请求FK，预留失败号不复用。 |

数据库责任：
- 静态部分唯一(task_id) WHERE state=ACTIVE；码摘要全表唯一；复合归属FK、scope/state/长度/时间和ACTIVE字段一致性CHECK。
- (created_at,id) WHERE ACTIVE支持队列，(expires_at,id) WHERE ACTIVE支持过期整理。
- 请求身份、task/user、范围、时间、epoch、hash不可更新，终态不能再激活，由受限写入口/数据库防误更新约束落实。不会拿动态时间谓词冒充唯一约束。
- 到期行未整理仍可能state=ACTIVE，但有效性必须另检查实时DB时间；生成事务先把旧过期行终止再插入。
- source用户是否USER、当前会话、任务30天边界等跨表/实时条件由统一授权事务验证，不能声称普通CHECK可查询其他表替代。
- terminal请求仅留最小身份/范围/时间/终态/hash至源任务created_at+30天或任务主动清理（取较早）；回执同样边界，删除task前显式清除。该期限为待批技术选择，非删除审计90天。
- 保留窗口内hash唯一并防旧值重用；窗口外依赖160位随机的密码学碰撞概率，不偷偷建立无限期历史码目录。随机性由CSPRNG源码审查和强制碰撞测试证明机制，不以“抽样没有重复”冒充证明。
- 授权/访问审计建议沿原TASK_30D自源任务创建起30天并随主动清理；删除任务/请求/部署者再推进等删除操作事件各自发生起90天。采用无请求/task FK的必要ID快照，避免阻塞清理；不记录码/hash以外无必要凭证。

## 2. 状态机与有效性

| 操作 | 状态/事务结果 |
| --- | --- |
| CREATE | 无有效当前请求才新建ACTIVE；存在有效请求409，不隐式替换。 |
| REGENERATE | expected_request_id精确匹配当前有效请求；旧行REPLACED且清密文，新ID ACTIVE同事务提交。并发已变409。 |
| 用户撤销 | 精确ID ACTIVE→REVOKED，同事务清密文/写事件；旧ID不作用于新行。 |
| 管理员删除请求 | 精确ID ACTIVE→DISMISSED、清密文/写删除审计；源内容不变。 |
| 任务主动/到期删除 | 同一任务删除事务ACTIVE→TASK_DELETED并撤销读取，独立意图先行。 |
| 时间到期 | t>=expires_at立即有效性为false；整理以ID+状态CAS置EXPIRED并清密文，不按task_id误伤新行。 |
| 重复终止 | 已终态/不存在精确ID为幂等无操作；身份/源任务归属仍必须检查，不制造重复成功事件。 |

有效谓词：当前会话/角色真实有效且账户未停用；精确请求ID存在、ACTIVE、当前epoch、t<expires_at；请求(task,user)匹配任务；源用户仍为有效普通用户；任务未删除/独立意图未阻止/未过30天边界；scope覆盖内容。两个入口和所有内容类型共用此谓词，不把码解析结果当缓存权限。

## 3. 码、摘要、认证加密及密钥

【候选技术P01～P03；数据保留P04见§1，统一批准清单见评审报告】
- secrets.token_bytes(20)，160位；RFC4648 Base32无padding为32字符，8组4字符、39字符展示，允许复制完整码。
- 输入上限64字符；只接受完整32字符或严格8组4连字符格式。只修整首尾ASCII空白/字母大小写，拒绝内部空白/换行、Unicode同形字、0/1别名及未知字符，不随意删除任意分隔符。
- code_hash=SHA256(ASCII域vsa-support-code-v1+NUL+canonical_code)，32字节。码/hash均不记普通日志。
- hash唯一冲突或nonce预留冲突各最多5次重生；耗尽503，不回退递增序号。
- AES-256-GCM加密规范码，密文含16字节tag；AAD规范v1编码绑定version/deployment_id/auth_epoch/request/task/user/scope/created/expires，时间为UTC整数微秒并固定字段顺序/长度编码。
- 每次实际加密（含轮换重加密）之前独立事务预留随机96位nonce，仅WRITING且未达年龄/计数上限的真实key可新预留；DECRYPT_ONLY/RETIRED拒绝新预留（仅先前预留的在途生成按后述规则）。唯一登记提交后才encrypt；登记不随业务回滚删除，保留至真实key销毁。不能用request_id截断作为nonce，不能恢复旧计数后用旧key。
- key_identity必须对应不可复用真实AES key，keyring加载检测重复真实key换ID并拒绝；可存非秘密fingerprint辨识，不能暴露原密钥。DB只存引用，秘密以只读/run/secrets专用文件注入应用账户，不进.env输出、Git、前端、命令参数或审计。
- 解密后验证规范格式和hash/AAD/tag；缺key、错误tag或epoch失败则503/不可用，不回退明文/旧purpose。管理员展示/内容路径失败关闭；所有者原内容不受影响。仅旧解密key丢失时，所有者当前查询仍返回不含码的Meta和CODE_UNAVAILABLE，能够CAS撤销或在新写入key可用时重新生成；若写入key也缺失，先由部署者恢复有效新key，不能无限卡在要求未知expected_request_id。epoch/DB信任失效仍503，不开放受保护内容。
- 计划每30天或累计100000次nonce预留（先到者）轮换新真实key；达到上限拒绝新预留/生成并发出部署者信号，不影响原所有者内容及旧有效请求解密；新key写、旧key只读。旧请求不续期；无旧key未到期引用、无在途生成事务后才能退役/销毁并清其nonce登记。可选择提前统一终止旧key请求，但须在轮换方案明确并审核。
- 恢复强制新真实AES key及新独立epoch，旧key不可再加密。丢key不恢复/猜码，由用户重新授权，源任务保留。依赖采用成熟cryptography AESGCM API，具体兼容Python3.11版本/锁定哈希需WP03安装前核实，不在本轮安装。

仅支持部署者维护窗口轮换：停生成入口、排空在途事务、发布只读新keyring并重启相应服务，不实现热重载/在线热轮换；旧key只解密。退役也先排空并核实引用，以下键级锁是防误排空/误退役的数据库约束，而非新增热轮换承诺。

轮换并发锁图：nonce独立事务只锁key登记、原子预留/计数后提交，不持key锁反向拿source。业务事务在source用户→task→request→idem之后，最终插入前取得key FOR SHARE，检查真实身份、当前epoch、未RETIRED，持至COMMIT；已预留而key变DECRYPT_ONLY的在途生成可完成，仍用原记录时间/TTL，不延长。退役只取key FOR UPDATE，再用READ COMMITTED新语句查有效请求引用；有引用不退役，绝不锁source/task/request。生成先赢则退役看到已提交引用；退役先赢则生成安全失败回滚。旧keyring缺失不会阻止core所有者API启动，但support模块/对应请求失败关闭，参见D的分层就绪。

nonce独立预留是无内容执行控制，不是新授权；冲突/事务回滚允许留未使用预留。唯一保证依赖当前受控key身份及登记不回滚；受控恢复更换真实key，不声称旧DB快照本身能保证nonce不复用。

## 4. 时间、锁序和原子生成（P05）

【提案】源用户→源任务→精确请求→幂等记录→最终crypto-key共享行锁。现有视频调度可能按队列积累多用户锁，当前账户停用为user→sessions；新支持事务不锁actor user/session，不引入反向锁。actor在入口、获得源锁后、最终响应/分块前用READ COMMITTED新语句读真实session/role/disabled，不信Principal快照。

创建操作：
1. 验证字段/当前身份/CSRF/Origin；解析和列表独立限速事务见第8节。
2. 取得源用户/任务锁，重新认证；检查所有权、USER、当前epoch、删除意图/任务状态和30天边界。
3. 先查幂等回执。同key同规范体且原ID仍有效，200原码/原created/expires；同key异体409；原ID已失效409，不转而创建。规范体绑定版本、operation、scope、expected ID，主体/任务由回执键绑定。
4. CREATE发现有效当前请求409；REGENERATE需精确expected有效ID，否则409。过期/已撤销后重新授权使用新CREATE和新request_key。持源任务锁检查最近5分钟成功生成回执数量，达到5次则429；有效同key重放先返回、不计新生成，失败事务无成功回执，不涉及视频配额。
5. 准备候选/独立nonce预留，锁后用DB clock_timestamp采样t，expires=t+86400秒。保存点内旧终止/新插入/回执/最小审计整体写，碰撞回滚该保存点再重生；最终COMMIT失败则全部业务变更回滚，nonce废号保留。
6. 提交成功才对外生效；最终复核原ID仍有效才展示。期间已被替换/删除则409不显示为当前码。

成功创建语义是“服务端记录时刻所在事务成功后才生效”，不是收到HTTP响应后补24小时，也不把等待锁的开始时刻当创建。事务正常受现有5秒DB等待限制；COMMIT延迟消耗TTL，不补足/续期。权限采用真实clock_timestamp，不用事务起点now，不依赖定时整理。服务端UTC/NTP为时间依据，客户端时区不影响86400秒；服务器时钟失常须部署者诊断，不能据重启重置期限。

## 5. API定义（路径无/api反向代理前缀）

公共RequestMeta：request_id/task_id/user_id/scope/state/created_at/expires_at；响应含server_time。额外字段禁止（422）。code只在成功生成/当前查询/有效列表出现，不出现在日志或后续URL。

| 方法/路径 | 请求 | 成功响应/语义 |
| --- | --- | --- |
| POST /tasks/{task_id}/support-requests | operation CREATE或REGENERATE、scope、request_key UUID；REGENERATE必填expected_request_id，CREATE为null | 201 {request:Meta+code,server_time}；有效幂等重放200且Idempotent-Replay:true。 |
| GET /tasks/{task_id}/support-request | 当前所有者USER | 200 {request:有效Meta+code或null,server_time}，不返回历史码；若仅该请求解密key不可用，所有者仍得到Meta、code:null、code_available:false、warning:CODE_UNAVAILABLE以能按精确ID撤销/重生；管理员自授权相关路径403。 |
| DELETE /tasks/{task_id}/support-requests/{request_id} | 所有者USER，CSRF | 204，精确ID撤销；不存在/终态幂等，无其他请求副作用。 |
| POST /admin/support-requests/resolve | body {code}，管理员认证+Origin/CSRF | 200 {request:Meta,server_time}，单条、不签发新bearer、不延长权限。 |
| GET /admin/support-requests | limit默认20/max100；cursor=(created_at,id) | 200 {items:[Meta+code+task_metadata],next_cursor,server_time}，只有效普通用户请求。 |
| DELETE /admin/support-requests/{request_id} | 当前管理员+CSRF | 204，移除/失效/事件同事务，原任务不变。 |
| GET /tasks/{id}/result或export或video | 原所有者路径 | 保留；含ADMIN所有者，无需排障码，仍验证当前会话/归属/删除。 |
| GET /admin/support-requests/{request_id}/result或export | language=zh或en，默认zh | 同现有结果/JSON内容契约，精确请求及scope检查；不接受purpose或code。 |
| GET /admin/support-requests/{request_id}/video | 现有单Range语法 | 200/206；先授权再读长度/Range，不泄露未授权对象长度。 |
| 旧/tasks/.../consents及/admin/tasks/{id}/...purpose路径 | 认证后固定拒绝 | 切换后410，且DB侧旧写入阻止；不对含purpose旧请求回退。 |

新排障内容路径即使actor恰好为owner也不绕过request检查；所有者使用原路径。管理员请求ID内容不接受额外task_id参数去选择别的任务。摘要未生成仍409，不因此授予视频。

task_metadata仅task_id/status/submitted_at，不默认给文件名/邮箱/正文/存储位置。队列时间/ID升序游标不含code，不是权限令牌；每次重新过滤期限/状态/epoch/源任务及独立journal。跳过失效候选后用最后扫描位置推进游标，返回前再次过滤刚失效行，防解密期间过期仍显示有效。后台每60秒整理只优化记录，不决定访问有效。

已送达旧列表不能远程抹除；客户端按server_time/expiry禁用、变更后刷新，操作始终后端重查，不把15秒页面刷新当安全撤销保证。

## 6. 范围与身份矩阵

| 主体/上下文 | 双语摘要 | 双语JSON | 视频/Range |
| --- | --- | --- | --- |
| 已认证所有者USER或ADMIN，原所有者路径 | 是 | 是 | 是 |
| 非所有者普通用户 | 否 | 否 | 否 |
| 当前ADMIN+有效summary请求 | 是 | 是 | 否 |
| 当前ADMIN+有效video请求 | 否 | 否 | 是 |
| 当前ADMIN+有效both请求 | 是 | 是 | 是 |
| 无请求/终止/到期/旧epoch/任务删除/会话失效 | 否 | 否 | 否 |

## 7. 流式与最终复核

沿用单Range和256KiB块。新短事务分别在读取块前和read返回后/yield前复核完整谓词；不持DB行锁跨网络I/O，不缓存ACTIVE，不使用公开/长期签名媒体URL。JSON/result/export返回前也复核。将来HEAD/条件请求若支持必须同样鉴权，不用304绕过当前权限。

最后授权检查为该响应/块的线性化点：失效提交之后发生的新检查必须拒绝；失效发生在最终检查后已交网络/缓存的最多一块在途数据不能原子追回。流headers已发时终止并close，不伪造403正文/补齐数据；客户端观察到短响应，后续请求拒绝。Cache-Control:no-store，页面卸载/退出清内存，不落local/sessionStorage、URL或埋点。

## 8. 错误、限速与脱敏（候选P06）

统一{error:{code,message}}，不回显输入、堆栈、Key、码、SQL绑定参数。
- 401未登录/失效会话；403角色、自授权、CSRF/Origin失败。
- 404 REQUEST_UNAVAILABLE统一无效码/请求不存在/到期/撤销/替换/删除/目标不可访问；不区分这些状态给枚举者。
- 409当前已存在、expected已变、同幂等键异体、原幂等请求已终止、结果未就绪/不合法。
- 422格式/额外字段/UUID/枚举/游标；416仅授权后无效Range。
- 429含Retry-After；503依赖/epoch/密钥/加密不可用，fail closed。

| 限速项 | 待批准候选 |
| --- | --- |
| 码解析总量 | 每管理员60次/分钟，来源300次/分钟，来源不信伪造X-Forwarded-For，不只依赖IP。 |
| 无效解析 | 每管理员10次/15分钟，达到后暂停15分钟，暂停期间有效查询也受限。 |
| 新生成/重新生成 | 每任务5次新生成/5分钟；幂等有效重放不算新生成，但仍受常规请求保护；不改变视频处理额度/重试。 |
| 列表 | 每管理员60次/分钟；limit最大100。 |

解析/列表固定窗口计数及blocked_until持久化并发原子更新，独立事务提交，无效码异常不能回滚计数。生成限制用源任务锁内最近5分钟成功回执滚动计数，幂等先判，不另取全局限速锁；失败回滚不计成功生成，也不扣任何视频机会。

未知码无target_user_id，不能为塞进现有audit表而伪造目标；用脱敏安全事件/限速记录保存actor、分类、时间和结果，不存码。禁止body/debug/SQL参数日志；默认访问日志只有固定路径/状态，不记录解析body。有效请求访问审计含管理员、请求/任务、操作分类、时间和结果，无正文/码。

## 9. 与删除/恢复交接及技术依据

任务删除的重复已有job路径和journal重放也必须补齐ACTIVE请求终止，不得在旧早返回位置漏做。所有入口读独立删除意图，DB事务失败不放行。请求/回执先于task显式清退，nonce登记与用户内容脱钩，按key生命周期清理。切换/恢复细则见[D](CR-WP01_CUTOVER_ROLLBACK.md)，后台清理和审计见[C](CR-WP01_DELETION_OPERATIONS.md)。

官方核对（仅技术资料，无项目内容外发/模型请求）：
- [PostgreSQL唯一/部分索引](https://www.postgresql.org/docs/17/sql-createindex.html)：动态时间不能作为不可变索引谓词。
- [数据库时间](https://www.postgresql.org/docs/17/functions-datetime.html)、[READ COMMITTED](https://www.postgresql.org/docs/17/transaction-iso.html)：锁后新语句和真实时刻。
- [Python secrets](https://docs.python.org/3.11/library/secrets.html)、[AESGCM](https://cryptography.io/en/stable/hazmat/primitives/aead/)：安全随机及nonce/AAD约束。

以上提案供产品负责人逐项批准；未运行DB/加密/流式或回归测试，Agent设计审阅不是实现验证。
