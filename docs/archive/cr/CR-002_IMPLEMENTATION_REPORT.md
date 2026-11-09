# CR-002 实施与验证报告

日期：2026-11-06。依据负责人正式审批、[最终契约](../../CR-002.md)与[独立测试规格](../../CR-002_TEST_PLAN.md)。

## 最终权限与产品行为

| 身份/入口 | 本人内容 | 他人内容 | 非内容元数据 |
|---|---|---|---|
| USER 正常任务路径 | 允许，实时会话/角色/归属/保留期/独立删除控制通过后 | 拒绝 | 仅本人 |
| ADMIN 正常“我的任务”路径 | 同USER，保留双语摘要/JSON/视频Range和seek | 拒绝 | 本人 |
| ADMIN /admin/tasks及元数据页面 | 不提供内容，即使本人任务 | 不提供内容 | 白名单任务状态/时间/失败代码分类/重试/配额/模型统计 |
| 排障请求、旧consent/purpose、旧管理员内容API/helper | 退役，不回退owner | 退役，无历史请求例外 | 排障定位/队列也退役 |

负责人最终审批取代最初影响分析中“管理员本人也拒读”的建议；实现未加入全局actor.role=USER限制。管理员特殊配额、任务处理/取消/重试/删除保留。

## 实际实现

- 通用session与owner联合查询从排障模块移至content_auth；当前DB角色须与本次Principal相符。summary/export最后复核、视频HEAD/get_object/read前后复核保留。
- support_requests缩为认证/角色/CSRF保护的410墓碑；results的purpose/request_id参数拒绝，不转接owner。
- legacy_support只保留关闭历史请求、support_version推进及DELETION_90D无内容审计；cleanup仅改导入。历史请求/consent外键清退和deletion_control恢复逻辑不变。
- /admin/tasks保留数组响应和字段白名单。task_id canonical完整UUID优先精确查询，其余仅hex/hyphen字符串、至少8个hex、最多36字符，参数化包含匹配；后端全局查询，不过滤前端当前页。limit1..100、offset0..10000、submitted_at/user_id/id稳定排序；2秒statement_timeout，每actor60/source300每60秒持久限速，非法422/超限429/数据库故障503。旧user_id筛选422。
- 前端删除排障申请/队列及三scope/倒计时/撤销/再生交互。ADMIN以“我的任务/任务元数据”互斥切换，进入元数据卸载个人导入、任务、结果和视频；账号/角色/CSRF边界同步回个人场景。管理元数据无结果组件和内容按钮；TaskPanel、ImportForm、ResultPanel展示并复制真实服务端Task ID。真实成功态仍SUCCEEDED，未新增COMPLETED。
- GET /config/public仅返回SUPPORT_EMAIL的合法邮箱或null；部署示例默认空。页脚位于正常文档流，配置缺失/非法不显示，mailto不附带身份或正文。真实邮箱仅放隔离人工环境运行配置，不硬编码业务源码。
- 切账号/角色/CSRF边界卸载、迟到响应隔离、视频停止及临时下载URL清理保留。DELETE202文案仅说明接受后台清理。

## 迁移、历史与变更边界

001～011不新增、不重写、不删除、不重排；010结构作为清退兼容残留而非授权依据。旧WP02～WP06验证报告及原始证据保留原文，新结果不能覆盖旧结论。正式业务库应用版本本次未连接核实。

没有执行Git操作、读取真实.env/模型密钥/用户视频摘要、真实模型计费、正式迁移或M3/WP07/S3-11/S3-12。测试仅隔离PG17、私有RustFS、合成账号视频和FakeModel。

## 职责与独立安全

Main先冻结135份起点文件SHA及契约/白名单，Backend Coding与Frontend Coding各自拥有实现文件，Testing独立拥有测试与隔离工具；Security独立只读审查权限、旧旁路、搜索/泄漏与前端边界，不以Coding静态检查代替运行证据。

Security S-01发现原owner路径既有的journal运行中身份失信仍可能放行缺口。最小修复仅在content_auth每次owner复核复用identity_ready；失信抛JournalError，由现有HTTP映射503、视频捕获并停止后续块。不修改删除机制、不重建控制、不新增迁移。定点复核及运行证据见新版WP06报告。

## 独立执行结果

最终正式执行：完整非模型后端 **312 passed / 0 failed / 0 skipped，153.08秒**；010结构专项 **42 passed / 14.84秒**；真实Linux **6 passed / 36.83秒**；真实HTTP集成 **7 passed / 51.95秒**；Mock Chrome **12组通过**；真实Chrome **7组通过**。各层有交叠，不相加成总数；先前定点和诊断不累计。

[新版WP06验证报告](CR-002_WP06_VALIDATION_REPORT.md)、[独立Testing报告](CR-002_TESTING_REPORT.md)、[原始执行证据](../../baseline/cr-002-testing.json)、[新版人工验收指南](CR-002_WP06_MANUAL_ACCEPTANCE.md)。自动化已完成，负责人尚未人工验收，不启动WP07。

独立Security初审与两次定点复核完成；S-01静态关闭且8种运行中控制失信负例通过，App独立场景复核通过。业务实现由Coding完成，Testing未修改业务代码来让断言通过。

## 交付、风险与WP07

实际文件变更/删除及before/after SHA见[最终差集清单](../../baseline/cr-002-changes.json)；旧证据和迁移冻结校验另列。测试删除/替换/保留清单由独立Testing报告说明，保留等价安全目标。

Main最终核验：起点135份文件中，23份按白名单修改、6份授权删除、106份逐字节保留；另新增17份交付文件（此处不含SHA清单本身）。001～011共11份迁移及旧报告/原始证据/核心删除运行保障共33份冻结资产全部SHA一致，无白名单外变更。可重建dist、依赖和缓存不计入源码交付清单，真实.env未读取。

旧人工环境与自动化资源已按身份/标签精确回收。最终人工环境vsa-wp06-172ff1a984保留，UI为http://127.0.0.1:50658，5容器/3卷/1网络/1代理；三个合成账号各有一份成功任务，实际邮箱只在运行配置。Main复核就绪、反馈配置及素材存在。自动化已完成并停止，等待负责人正式人工验收。

正式切换必须在新版WP06负责人验收及独立WP07授权后进行：维护门禁与旧服务/连接/流排空、两类DB身份隔离、旧purpose及残留请求终止、USER和ADMIN本人内容正例/所有跨用户负例、元数据无内容、旧API拒绝、删除重放和最低故障发现。失败维持维护，只回兼容当前owner语义的版本，不回旧排障授权实现。

真实模型质量/供应商费用延迟、生产持续容量、生产非root策略、真实旧服务/凭据失权与历史基础设施副本处置不由本次替身/隔离测试证明。已发送或下载的客户端副本不能追回。
