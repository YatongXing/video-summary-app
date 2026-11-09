# CR-WP01-A/B：请求数据、API与权限契约

> **历史排障接口设计。** 下文的请求生成、版本冲突、范围和续传授权保留作 WP02/WP03 追踪。现行内容及元数据接口见 [CR-002](../../CR-002.md)，旧接口不应恢复。

**版本：** L1.0 · **日期：** 2026-11-04
**状态：** L-01/L-02、R-01/R-02已批准，R-07为WP03初始限速；替代AB0.1～AB0.4的访问码/专属加密/回执设计。仅文档，WP01 L1.0已正式验收，WP02未授权。
**关联：** [共享契约](CR-WP01_CONTRACT.md)、[删除运维](../../CR-WP01_DELETION_OPERATIONS.md)、[切换回退](CR-WP01_CUTOVER_ROLLBACK.md)。

## 1. 数据模型、约束与保留

support_requests是请求及同次授权唯一权威，id每次新建，旧ID不按task追随新请求。

| 字段 | 类型/约束 |
| --- | --- |
| id | UUID PK，服务端UUIDv4；仅定位、非凭证，不可改/复用旧ID。 |
| task_id,user_id | UUID NOT NULL，复合FK到tasks(id,user_id)，RESTRICT，不级联删除源任务。 |
| scope | NOT NULL，summary/video/both，创建后不就地变更。 |
| created_at,expires_at | timestamptz NOT NULL；expires严格等于created+86400秒，不用日历1 day。 |
| closed_at,close_reason | 未关闭时均NULL；关闭时同时非NULL，时间>=created，固定原因REVOKED/REPLACED/DISMISSED/TASK_DELETED/EXPIRED。关闭不可逆。 |
| tasks.support_version | bigint NOT NULL DEFAULT 0且>=0，正常运行单调，变化时+1；不是授权epoch，受控恢复边界见D。 |

数据库落实：UUID/非空/范围/时间/关闭一致性CHECK、复合FK；部分唯一(task_id) WHERE closed_at IS NULL；主键、队列(created_at,id)未关闭部分索引、(task_id,created_at)频率/清退索引。身份/归属/scope/创建和期限字段不可修改、关闭不可回开放、任务版本不得回退，由新增迁移中的数据库防误更新约束/触发器和受限写权限落实；跨表角色/实时期限仍由服务端检查，普通CHECK不能代替。

过期但未关闭行仍占唯一槽，新CREATE事务先将其关闭再插入。有效性实时判断，不建动态now索引、专用到期Worker/索引。不创建code_hash/code_ciphertext、专属key/nonce表、generation receipt、额外授权epoch/协议状态表。密码哈希、会话、CSRF、存储/模型秘密保护保留。

请求及终态最小字段至源任务created_at+30天或主动清理较早者；无历史请求页面。删除任务/请求/维护操作的必要无内容事件各自90天；普通访问事件沿TASK_30D源任务期限，未知ID不伪造target_user_id（脱敏分类日志/既有限速记录即可）。任务清退前显式删请求，审计用必要ID快照，不阻内容清理。

## 2. 状态、时间与锁序

持久状态由closed_at表示，界面派生有效/已关闭/已过期，原因不另造状态机。有效谓词包括：真实会话/role/disabled、精确请求/任务/用户、未关闭、DB真实时间<expires、源普通用户有效、任务未到30天/未删/无独立删除意图、scope符合。请求编号不替代任一检查。

锁序源用户→源任务→精确请求；不反锁actor/session，入口、源锁后和最终读取前用READ COMMITTED新语句复核身份。created_at在取得必要锁后用clock_timestamp记录，expires=created+86400秒；提交成功才生效，提交/响应延迟消耗期限，不续期。恰好到期拒绝，不等整理；任务30天先到则先失效。

| 操作 | 事务语义 |
| --- | --- |
| CREATE | expected_version匹配且无有效当前请求；关闭旧过期行，新UUID/时间、support_version+1和事件同事务。 |
| REGENERATE | expected_version和expected_request_id均精确匹配当前有效请求；旧REPLACED、新UUID、新24h、版本+1/事件原子提交。 |
| 用户撤销/管理员删除请求 | 首次关闭精确当前ID、版本+1/审计同事务；原任务/新请求不变。 |
| 重复DELETE/旧ID | 核验角色/归属后204无操作，不再+1或重复成功事件，不关闭任务的新请求。 |
| 任务主动/到期删除 | 独立意图优先；栅栏/代次、关闭请求与必要版本更新同事务，已有job路径和重放也补齐。 |
| 到期 | t>=expires立即无效；可在下次操作/保留清理关闭EXPIRED，不必为整理再加版本。 |

同任务唯一约束兜底并发；提交失败回滚请求/关闭/版本/审计全部业务变化。版本不因服务重启或正文清理重置。

## 3. 无回执的冲突与恢复

CREATE和REGENERATE均必填expected_version；REGENERATE另需expected_request_id。CREATE有当前有效行409 ACTIVE_REQUEST_EXISTS，版本或ID变化409 SUPPORT_VERSION_CONFLICT/EXPECTED_REQUEST_CHANGED；不隐式替换。

重复POST可一次201、其他409；响应丢失/提交未知时GET current核对实际状态。客户端不得自动取新版本重发旧创建/替换；用户新的明确操作才获取新前置条件。若确认原事务未提交且版本未变，可由用户确认重试原前置条件。GET后又发生变化仍409。

这阻止“CREATE成功丢响应→撤销/到期→旧POST迟到再次授权24h”：原expected_version已因首次创建过时。无回执后只承诺恢复当前实际状态，无法精确证明某个历史POST提交与否或重放其原响应；不另建同功能操作表。

## 4. API与错误

路径省略/api代理前缀。全部POST/DELETE须有效会话、当前角色/归属及既有CSRF/Origin验证；UUID/版本不替代这些检查。RequestMeta为request_id/task_id/user_id/scope/created_at/expires_at及派生有效状态；响应含server_time。拒绝多余字段；不再接受code/purpose/request_key。

| 方法/路径 | 输入/结果 |
| --- | --- |
| GET /tasks/{task_id}/support-request | 所有者普通用户；200 {request:有效Meta或null,support_version,server_time}。 |
| POST /tasks/{task_id}/support-requests | {operation:CREATE或REGENERATE,scope,expected_version,expected_request_id?}；CREATE不接受替换ID，REGENERATE必填；201返回新Meta+版本，重复/冲突409后GET，不重放历史响应。 |
| DELETE /tasks/{task_id}/support-requests/{request_id} | owner普通用户+CSRF，204，精确关闭/幂等；管理员自授权相关接口403。 |
| GET /admin/support-requests | 当前ADMIN，limit20/max100，cursor(created_at,id)，按升序只列有效普通用户请求；items含UUID/用户标识/任务ID、状态、submitted_at/范围/期限，不含正文/文件名/邮箱/存储位置。 |
| GET /admin/support-requests/{request_id} | 当前ADMIN按编号定位单请求，200 Meta；失效/不存在统一404；不签发bearer。列表与手动编号共用后续精确ID逻辑。 |
| DELETE /admin/support-requests/{request_id} | 当前ADMIN+CSRF，204关闭/撤权/事件原子提交，不删原内容。 |
| GET /tasks/{id}/result或export或video | 保留原owner路径，USER/ADMIN所有者均无需排障请求。 |
| GET /admin/support-requests/{request_id}/result或export | language=zh/en，默认zh；精确请求/scope实时核验，结果未就绪409。 |
| GET /admin/support-requests/{request_id}/video | 现有单Range语法；授权后才读取长度/处理Range，200/206。 |
| 旧consents及/admin/tasks/{id}/内容purpose路径 | 正式切换后认证后410，旧DB权限撤销，不回退目的授权。 |

401会话失效；403角色/自授权/CSRF；404管理员请求不可用或无权目标；409当前状态/版本/ID/结果冲突；422格式/额外字段；416仅已授权Range；429含Retry-After；503依赖/维护门禁/控制失信。错误不回显输入/堆栈/秘密。向owner给明确冲突分类，不能向无权主体泄露目标状态。

## 5. 权限、列表与续传

| 主体/上下文 | 双语摘要 | 双语JSON | 原视频/Range |
| --- | --- | --- | --- |
| 有效USER/ADMIN所有者原路径 | 允许 | 允许 | 允许 |
| 当前ADMIN+有效summary | 允许 | 允许 | 拒绝 |
| 当前ADMIN+有效video | 拒绝 | 拒绝 | 允许 |
| 当前ADMIN+有效both | 允许 | 允许 | 允许 |
| 其他普通用户/旧ID/过期/关闭/任务删除/会话无效 | 拒绝 | 拒绝 | 拒绝 |

新请求内容路径不因actor恰好是owner绕过请求检查，owner使用原路径。旧ID绝不查task的最新请求。管理员列表/编号/内容都服务端鉴权，无公开分享链接；UUID可在受保护URL，日志仍不附正文/凭据或不必要关联信息。

列创建时间/ID升序，每页实时过滤及返回前复核，跳过失效记录后游标用最后扫描位置；失效退出，重生新ID按新时间排队。已送达列表不能远程抹去，客户端按server_time/expiry提示与刷新，后端逐次拒绝是安全保证。

视频沿用256KiB块读前/读后yield前完整鉴权，result/export返回前也复核；不跨网络持DB行锁，不发公开长期媒体URL。最终检查为该响应/块线性化点，已经交网络的最多一块在途字节不能追回；失效时close短响应、不伪造403正文。no-store、登出/切账号清内存，旧异步响应不得覆盖新版本；未来HEAD/304若支持同样实时鉴权。

## 6. R-07初始限速

| 项 | 已批初始配置 |
| --- | --- |
| 管理员编号查找 | 60次/管理员/分钟；来源300次/分钟，不信伪造代理来源。 |
| 管理员列表 | 60次/管理员/分钟；来源300次/分钟；默认20、最多100。 |
| 新CREATE/REGENERATE | 每任务成功5次/滚动5分钟，从support_requests创建记录计数；GET恢复不新生成/续期，不影响视频额度。 |

取消无效码10次/15分钟暂停15分钟的密码式惩罚。沿用auth_limits新scope，查找/列表计数持久原子、独立事务，不因错误回滚；生成预算在源任务锁内查请求表，同事务失败不计成功。WP03/06隔离结果如需调参提交审核，不自行变更。

## 7. 实施/历史边界

WP02只010结构、tasks.support_version及独立测试；WP03实现本API和权限，真实M3留WP07。原AB0.x的code/key/nonce/receipt/epoch部分取消，不将其历史审查关闭称为本契约已验证。当前未执行新业务测试。
