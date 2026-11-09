# CR-WP01 共享契约与协调记录

> **历史 L1.0 契约。** 旧排障能力随 [CR-002](../../CR-002.md) 退役。未替代的删除与维护设计继续适用，首次部署以 [WP07-A](../../CR-WP07A_LOCAL_DEPLOYMENT_PLAN.md) 为准。

**版本：** L1.0 · **日期：** 2026-11-04
**效力：** L-01～L-05及R-01～R-07已批准；R-03/R-05附条件，R-04具体清理字段在WP04前冻结。本文件及A/B、C、D为当前设计基线，替代C0.1～C0.4中被取消的技术部分。
**授权：** 仅文档同步；WP01 L1.0已由产品负责人正式验收，WP02～07未授权。

## 1. 当前规则与单一契约

依据[CR-001](CR-001.md)、[ADR-005](../../adr/ADR-005-cr-001-deletion-troubleshooting.md)。L/R批准落实、去留矩阵和本轮交付见[评审报告](CR-WP01_REVIEW_REPORT.md)。旧设计/测试/安全报告中的历史结论不构成当前crypto/回执/多角色要求。

- 单一support_requests承载请求及同次授权；id为服务端UUIDv4定位符，不是密码/凭证。task/user复合归属FK；每任务最多一未关闭记录，只有未关闭且实时未到期、源任务可访问才有效。
- scope为summary/video/both；summary含zh/en摘要及JSON，不含视频。所有者USER/ADMIN走原路径直读；其他用户内容须当前ADMIN+精确请求ID+scope/24h/任务/用户/删除检查。
- 字段为id/task_id/user_id/scope/created_at/expires_at/closed_at/close_reason；不建访问码、hash/ciphertext、nonce/key登记、生成回执、专用授权epoch/协议表。
- tasks.support_version正常运行期间单调；CREATE/REGENERATE必填expected_version，REGENERATE再必填expected_request_id。源用户→任务→请求锁序，事务内改变授权和版本/审计；同任务唯一约束兜底。
- CREATE不替换有效当前请求；REGENERATE原子关闭旧行/新ID新24h。重复或陈旧POST409→GET current，客户端不得自动刷新版本重发旧操作；恢复承诺是当前实际状态，不是历史POST原响应。
- DELETE精确ID；首次关闭与版本+1/事件同事务，旧ID/重复删除无额外作用，不伤新请求。任务删除先独立意图、同事务删除栅栏/代次/请求关闭；已有job早返回及重放必须补齐。
- created_at锁后DB clock_timestamp记录，expires=created+86400秒；事务成功才生效，响应/提交延迟不补TTL。t>=expires立即拒绝，不等后台归档，不延长任务30天保留。
- 当前用户GET、POST、DELETE；管理员GET列表/精确编号/内容及DELETE。路径/响应/错误与固定初始限速以[A/B](CR-WP01_DATA_API_DESIGN.md)为准；无独立码解析服务。
- 列表仅有效普通用户请求，created_at/id升序；失效退出，替换新时间排队，无历史管理页；删请求不删原内容。
- 删除需真实生产停止/隔离和逐项验证。正常取消+task SH/EX为附条件候选，WP04真实Linux Docker/FFmpeg验证失败停止相关实现，不降为lease判断。
- 清理四态PENDING/RETRY_WAIT/BLOCKED/COMPLETED，单job_version、全局清理EX、持久退避及最低发现链；字段WP04前冻结，WP02不建011或v2影子。
- 两类DB权限：运行受限DML，维护兼结构/受控CLI且凭据不注入普通服务。独立删除控制与显式维护门禁保留；受控离线恢复统一终止旧会话/新旧授权，账户状态不明不开放。
- 任务/普通任务日志自创建30天，删除审计各事件90天；未完成控制独立保留。无新内容备份，历史BACKUP先核实批准，不虚报整体完成。

## 2. 实施与验证边界

010仅请求表、tasks.support_version、必要约束/索引和独立测试；001～009不改。011清理迁移后移WP04，开始前复核编号；M3正式切换仅WP07单独授权。详见[D](CR-WP01_CUTOVER_ROLLBACK.md)、[工作包](CR-001_IMPLEMENTATION_PLAN.md)、[测试](CR-001_TEST_PLAN.md)。

视频FIFO、普通用户同时1个、系统可配置并发N、配额/退款/重试及60分钟成功任务P90≤20分钟口径不变；清理全局1不等于视频并发1。旧S3-10结果只作历史，不宣称新需求已验证。

## 3. 协作与历史

默认Main/Coding/Testing：Main维护共享契约/派发/审核，Coding仅获准源码，Testing独立关键测试；文件所有权互不重叠。WP03/04/07定向临时只读安全复核，其余包有重大风险再提专项。不固定第四角色，每包结束停等产品负责人验收。

C0.1先冻结后原三独立设计/测试/安全审查、C0.2～C0.4问题响应的派发历史保留于旧评审记录；其中crypto/key/nonce/回执/epoch和多角色部分已被本基线替代，不继续实施。当前仅做批准内容同步，未再启动完整设计项目。
