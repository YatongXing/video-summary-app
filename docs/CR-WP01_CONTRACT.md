# CR-WP01 共享契约与协调记录

**版本：** C0.3 · **日期：** 2026-10-30  
**状态：** 主Agent为设计协作冻结的提案契约，不是产品负责人对技术参数的批准。WP00已被用户接受；WP01已获设计授权；WP02～07未授权。
**写入责任：** 共享文件只由主Agent维护。Sub-agent本轮只读并回传设计/发现，不写仓库文件。

## 1. 已批准规则

D-01～D-08、CR-D09～16以[CR-001](CR-001.md)、[ADR-005](adr/ADR-005-cr-001-deletion-troubleshooting.md)、[PRD](PRD.md)、[AC](AC.md)为准。所有者直读、单任务单有效码、成功创建起24小时、三范围、失效立即退出、请求删除不删除源任务、真实清理验证、90天事件审计和历史副本受控治理不可替换。

## 2. 冻结的设计接口（C0.3候选，允许评审挑战）

- 单一 `support_requests` 权威记录同时表示请求和同次授权，不另建可独立有效的内容授权。
- 身份键：`request_id, task_id, user_id` 为独立UUID；任务/用户复合外键。每次重新生成新request_id，内容读取固定该ID，绝不自动追随任务的新请求。
- 范围：`summary | video | both`；summary含zh/en结果与JSON，video不含摘要/JSON。
- 状态：`ACTIVE, REVOKED, REPLACED, DISMISSED, TASK_DELETED, EXPIRED`。只有ACTIVE且实时未到期、任务可访问时有效；后台整理延迟不得放行。
- 每任务ACTIVE行唯一约束，事务锁序源用户→源任务→请求→幂等；不锁actor用户/session，拿源锁后及最后读取前用READ COMMITTED新语句复核其真实会话/role/disabled/epoch，避免扩大现有队列锁图。
- 创建/重新生成均POST `/tasks/{task_id}/support-requests`，请求体operation=CREATE|REGENERATE、scope、request_key；REGENERATE必填expected_request_id，CREATE不得隐式替换。幂等先检查，同key不同体409，已终止重放409，不续期/复活。
- 用户GET `/tasks/{task_id}/support-request` 查询当前；DELETE `/tasks/{task_id}/support-requests/{request_id}` 撤销。
- 管理员POST `/admin/support-requests/resolve` 在请求体提交code；GET `/admin/support-requests`列有效请求，按(created_at,id)升序；DELETE `/admin/support-requests/{request_id}`结束该次授权。
- 所有者原 `/tasks/{id}/result|export|video` 保留；他人内容 `/admin/support-requests/{request_id}/result|export|video` 统一鉴权。码不进URL；request_id不是凭证。
- 候选码：160位随机/20字节，Base32为32字符、8组4字符展示；SHA-256域分离摘要查找，AES-256-GCM密文展示；独立提交预留96bit随机nonce/真实key身份直到key销毁，AAD绑定部署/请求/任务/用户/范围/时间/epoch。具体参数待产品批准。
- 成功创建定义为服务端获得必要锁后记录创建瞬间，事务提交成功才生效/展示；expires_at=created_at+24h，实时检查now>=expires_at拒绝。提交/锁等待造成的时间边界必须明确。
- 幂等键重复不能新建/续期，旧请求终止后幂等重传不能复活。撤销、管理员删除请求、替换、任务删除均和授权失效同事务提交。
- 原任务DELETE保留独立删除意图、代次栅栏、逐项验证；清理状态候选PENDING/RUNNING/RETRY_WAIT/WAIT_PRODUCER/BLOCKED_CONFIG/BLOCKED_UNSAFE/WAIT_LEGACY_APPROVAL/COMPLETED，不以产品人工页作退出路径。
- 重试/限速参数、运维命令、迁移/切换/回退均由WP01提出具体提案，不实施。源任务30天/删除审计各事件90天不改，其他审计期限要明确分开。

## 3. 评审/交接规则

主Agent先冻结C0.1，再派发设计S1、独立测试S2、安全S3。输入为WP00更新后的2026-10-30基线；当前HEAD dc257d148d2033b2d65a3470e11ae66a7ff50fe1且工作区未提交。三角色允许读相同文档/源码，允许写路径均为空；输出经消息回传，由主Agent整合到独立WP01设计/评审文档。

任何角色发现契约缺陷须标问题ID、反例、影响/AC和修订建议，不直接改契约。主Agent维护版本变更及裁决，受影响设计更新后再交S2/S3交叉审核。Agent完成不代表产品验收；最终状态只能是提交负责人审核，停止WP01，不启动WP02。

## 4. C0.1 → C0.2协调裁决（主Agent，待产品审批）

- 采纳S1-F01、S2-F01、S3-F10：显式CREATE/REGENERATE、expected_request_id及全体绑定幂等。
- 采纳S1-F02、S2-F02、S3-F06：源锁后实时DB时间/身份复核，actor不反向加锁；在途已发送字节不承诺原子收回。
- 采纳S2-F03/F06、S3-F02/F03：每task稳定inode，生产SH且子进程pass_fds，清理EX覆盖全部I/O；禁止仅凭claim/lease抢占或确认完成，先排空旧Worker。
- 采纳S2-F04、S3-F08：独立健康/进度检查发现维护线程阻塞，持久信号加部署者巡检，不仅看主进程存活。
- 采纳S2-F05、S3-F09：online/overall/基础设施核实分开；受控BACKUP未清不能overall完成。
- 采纳S1-F06、S3-F01：旧路由410+停止旧binary/连接+换最小DB运行角色；恢复新真实AES key和独立epoch，不能只改版本表。
- 采纳S3-F04/F05/F07：可信不可变存储定位、NoSuchBucket不视验证成功、禁止危险CLI；nonce独立预留及AAD、日志和限速独立提交。

以上是主Agent协调后的设计提案冻结，不代表用户批准参数或WP01验收；最终评审记录另见[评审报告](CR-WP01_REVIEW_REPORT.md)。

## 5. 第二轮交叉审核修订C0.3

S1-F10明确生产SH/清理EX都在DB行锁外取得、禁止反向等待；S1-F11明确core和support密钥故障的就绪边界。S2-F07补清理字段/类型/索引、旧状态转换、失败n及控制器进度。S3-F11补受限ops角色、回执优先及调度/审计同事务；S3-F12明确M3不是普通schema自动迁移，须WP07显式维护授权。crypto key注册/共享行锁与退役锁保证并发生成不被误退役。终态请求/hash及回执的保留边界、密钥丢失无码Meta恢复路径见AB0.3。

S1-F12补充：WAIT_LEGACY_APPROVAL仅限online已完成且唯一剩余阻碍为历史受控BACKUP批准，不能让旧人工停等转换阻塞尚未完成的在线资产。S3定点澄清：当前写key故障只禁生成，不阻已有请求用可用解密key访问；core所有者权限仍独立，失信epoch不降级。
