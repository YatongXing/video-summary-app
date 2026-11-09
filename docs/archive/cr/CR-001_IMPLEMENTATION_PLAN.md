# CR-001 独立工作包与实施准入

> **历史实施计划和交接。** 保留旧包授权、白名单与结果。当前状态及 S3-11/S3-12 顺序见[实施计划](../../IMPLEMENTATION_PLAN.md#当前交付状态)，正式部署按 [WP07-A](../../CR-WP07A_FORMAL_RUNBOOK.md)。

**版本：** L1.0 · **日期：** 2026-11-05
**状态：** WP00已接受，L-01～L-05/R-01～R-07已批准并同步；WP01 L1.0已由产品负责人正式验收，用户已单独授权启动WP02，请求结构实现与专用隔离验证完成并交付；用户已明确授权推进WP03，请求API/实时鉴权及独立隔离验证完成，WP03已获产品负责人正式验收，WP04已由负责人正式验收；WP05已正式验收，用户单独授权WP06系统验证/人工环境准备，WP07未授权。本轮不授予真实业务库迁移或M3切换权限。
**依据：** [CR-001](CR-001.md)、[ADR-005](../../adr/ADR-005-cr-001-deletion-troubleshooting.md)、[共享契约](CR-WP01_CONTRACT.md)、[测试](CR-001_TEST_PLAN.md)。

## 1. 顺序与共同门槛

WP00接受→WP01新基线正式验收→WP02→WP03→WP04→WP05→WP06→WP07→单独S3-11→单独S3-12。各包逐次授权/验收，包完成停等负责人，不自动连续推进。

每包开始需前置退出证据、当前契约/指纹、明确可写文件/测试资源与失败边界。独立关键测试使用专用可销毁PG17实例、*_test库、UUID schema、合成身份/媒体及模型替身；不读真实.env、不用真实API Key。真实M3/数据副本处置/Git保存各有专项权限，不默认建立含内容备份。

Main先冻结接口/路径/状态与文件责任；Coding/Testing可并行准备但不并写同文件。对共享契约的变更须Main评估并按批准范围处理，实质冲突停相关部分报告。

## 2. 工作包与准入/退出

| 包 | 开始条件与范围 | 独立验证/退出 |
| --- | --- | --- |
| WP00 文档基线 | 已获接受；保留历史交付。 | 原批准历史/S3-10证据不覆盖。 |
| WP01 最终基线同步 | L1.0文档基线已正式验收，本轮仅历史整理，当前L1.0替代旧技术部分。 | 一致性/链接/文件不变性；负责人已正式验收L1.0，WP02仍需单独授权，不运行业务测试。 |
| WP02 请求结构 | WP01 L1.0已验收且WP02已单独获代码/专用隔离测试授权；只010/support_version/约束/独立测试，白名单§3。 | 空库/001～009合成旧库、旧checksum、唯一/FK/时间/关闭不可逆/版本、两role/事务失败；旧purpose/内容/配额不变，无自动转码或终止。 |
| WP03 请求API/鉴权 | WP02已交付，用户本轮明确单独授权WP03；API L1.0冻结，实际白名单见§9，不额外更改依赖/部署配置。 | CREATE/REGENERATE/GET/DELETE版本CAS、409→GET、UUID列表/定位、owner/三scope/24h/Range/限速/旧purpose拒绝；独立测试及定向安全，无真实M3。 |
| WP04 删除/Worker/维护 | WP02/03验收+单独授权；R-04具体字段/映射在实施前冻结，011或下一个编号重查。cleanup/deletion_control/worker/queue/processing/preprocess及确需存储、清理迁移/测试/获准示例。 | 真实Linux Docker/FFmpeg父死子活/FD/inode/SH-EX、N>=2/删A不伤B、四态/单job_version/故障退避发现/三入口/事务审计/归属/90天/BACKUP审批；定向安全，失败停相关候选另审，不清真实数据。 |
| WP05 前端 | WP03和WP04契约/反馈验收稳定，单独授权组件/测试。 | UUID/三scope/期限/复制/列表排序/查找/撤销/再生/409恢复，owner直读，去删除历史/人工页；Coding前端、Testing独立浏览器，保留五状态/双语JSON，构建不代替后端权限。 |
| WP06 新回归/人工 | WP02～05完成并固定指纹，明确非模型隔离资源授权。 | 独立全后端/前端/真实HTTP+Worker+S3合成闭环、授权负载/故障/迁移/恢复预演，负责人本地人工；逐项通过/失败/未执行，新报告不改S3-10。 |
| WP07 正式切换/交接 | WP06验收，单独批准实例/时段/M3/旧授权终止/真实角色/维护通道与操作。 | 部署者执行，独立测试/定向安全核验；旧服务/连接失权、两角色隔离、维护门禁/受控恢复/账户状态、最低故障发现实际运行后才开放。切换失败不复活purpose，提交后停等验收。 |

R-03运行证明留WP04，R-05实际恢复/权限证明留WP07；不把未来测试提前要求在WP01执行。清理字段未最终冻结按R-04留WP04，不阻WP02请求结构；011已移出WP02，不再预建v2影子/双轨状态。

## 3. WP02最终拟修改白名单与隔离测试

下列为本次WP02启动授权采用的**实施白名单**；不扩大到后续包：
- Coding：backend/app/migrations/010_cr_support_expand.sql，仅support_requests、tasks.support_version、必要FK/CHECK/唯一/查询索引及防误更新约束。无需改models.py、业务数据访问或迁移器。
- Testing：backend/tests/cr_wp02_fixtures.py、test_cr_support_schema.py、test_cr_migrations.py、test_cr_roles.py，新独立夹具/测试；测试角色资产仅在专用实例。基本自检若另需文件，必须先列范围，不能并写Testing文件。
- Main：获准后维护派发/交接及docs/CR-WP02_VALIDATION_REPORT.md证据报告；本轮不新增该报告。
- 默认只读：001～009、app/migrate.py、tests/test_migrations.py、models.py；没有011/test_cr_cleanup_schema.py、清理影子/状态、API/Worker/前端/真实配置或M3。

开始前重新核对010未占用，已应用SQL不可覆盖/重排；将001～009 SHA与Git/隔离schema_migrations登记对照，差异即停，不补写checksum绕过。010首次应用后同样冻结。

专用可销毁PostgreSQL17实例、显式TEST_DATABASE_URL到*_test库/UUID schema、合成运行/维护两类权限；不回退DATABASE_URL、不读.env或连业务集群。角色属集群级，不能只用schema隔离；资源创建/回收须在未来授权中写明。

独立测试空库/旧合成全状态、多purpose/管理员自授权、重复迁移、篡改旧校验拒绝、复合归属/未关闭唯一/范围时间/不可改身份和期限/关闭不可逆/版本不可回退、双连接和提交失败。覆盖autocommit及已有外层事务保存点、双迁移者/失败重放；两role无DDL/SET ROLE逃逸、维护可重连；legacy task_consents仅允许任务清退DELETE/必要归属列读取，目的授权读取/创建/更新仍拒绝，避免旧FK阻塞正常清理。结构升级不自动转换/终止旧授权，不删除用户任务/结果/配额。

## 4. 默认三Agent与定向安全

| 角色 | 固定责任/文件边界 |
| --- | --- |
| Main | 计划/依赖/接口、统一共享文档、明确文件所有权、审核证据/指派修复、申请验收；不承担常规业务编码/测试实现。 |
| Coding | 获准模块/迁移/必要自检及修复，不改已批准需求/共享契约，不调用付费API或自动Git。 |
| Testing | 先从需求/AC独立设计，拥有独立测试/夹具，执行关键约束/权限/并发/故障和复测；不以Coding自检代证据。 |

WP03认证/授权/资源隔离、WP04删除/并发/危险维护、WP07正式切换/旧授权/部署恢复安排临时定向只读安全复核。其余重大风险由Main提出专项，不固定第四角色。细小任务不强制全员参与，关键测试保持独立。

每次派发含：WP/子任务ID、输入版本、模块/允许路径、冻结契约、必须任务、禁做、AC/E2E/T、交付证据、依赖/交接九项。Testing报缺陷→Main分配Coding修复→Testing独立复测，无多人同时写同文件。Agent完成不代表负责人验收。

## 5. 当前Git状态与安全保存建议（仅建议，未执行）

WP01同步时的历史起点为implementation HEAD96b093afed1ef0cb36685ba46325d52c805d612c，已有9份tracked文档修改及2份untracked评审文件，均在原位继续保留。早期dc257d/17项/59项为历史，不作为当前基点。

该段为WP01历史保存建议；WP02当前实施记录见§8，同样不执行commit/push/stash/reset/clean/checkout。日后获保存授权才按明确路径白名单暂存/复核，排除真实.env/密钥/媒体/数据库对象卷/日志/缓存，保留软件资料与指纹；不标未经负责人验收的WP01为已验收。推送另授、仅快进。Git不能恢复用户内容，不为切换建立含内容备份。

## 6. S3-11与S3-12衔接

WP07验收后另授S3-11和每个计费批次；先验证任务限定可撤销供应商媒体路径，请求编号不能充当模型凭证，供应商临时资产纳入删除。15授权视频按负责人UI手动评测，真实质量/费用/性能未由替身证明。

S3-11完成/负责人接受限制后另授S3-12：同源静态部署、私有持久卷/网络秘密/升级、管理员交付、监控完善、避免新含内容整库/快照、历史清点隔离及专项获批退役、无内容软件重建资料与不可恢复风险。最低故障发现已经是WP07开放前门槛。历史能力退役须实际证据及专项授权，未知不改成功。

## 7. 当前退出

WP01 L1.0已正式验收，见[评审报告](CR-WP01_REVIEW_REPORT.md)；WP02已交付后，用户另行明确授权WP03。WP03当前实现与独立验证完成，证据见§9，已获负责人正式验收；WP04及WP05已正式验收，交付见§10/11；用户单独授权WP06，自动化验证及人工环境准备见§12和本包报告，待负责人人工验收/正式决定，WP07未授权。R-04字段冻结、R-03真实生产者停止与R-05实环境恢复门槛仍留对应工作包，不由本包测试替代。

## 8. WP02启动与交接记录（2026-11-04）

用户明确“现在开始WP02”，本包依L1.0白名单实施。Main/root先冻结字段/索引/触发器/期限与旧数据不变边界，再派发：
- WP02-Coding，/root/wp02_coding：仅010_cr_support_expand.sql；不改旧SQL/迁移器/models/路由/Worker/配置或测试文件。
- WP02-Testing，/root/wp02_testing：仅四个新独立测试/夹具；从AC/T规格独立设计，专用PG17内应用验证010，缺陷经Main指派Coding修复，不能自行改SQL。
- Main：维护契约/计划与[WP02证据](CR-WP02_VALIDATION_REPORT.md)、资源边界/基线指纹和交付审核。无默认第四安全角色。

专用PG17容器使用本机随机端口、tmpfs数据、合成身份，无业务数据卷；仅在此创建/回收测试schema和两类角色，最终回收本包容器。各派发含任务ID、L1.0输入、模块/路径、冻结契约、必做/禁做、AC/T、证据和交接依赖。实现完成不代表包验收，WP03～07/M3/S3-11/12不随此授权。

WP02交接结果：42项新增独立测试+10项原迁移测试通过，角色1项定点复测不重复计数；专用测试schema/role及PG容器已回收。010及证据指纹见报告。当时Main提交包验收建议并停止；此后用户明确授权WP03，见§9，不追记未经明确确认的验收。

## 9. WP03启动、白名单与交接（2026-11-04）

用户正式授权WP03，Main按L1.0锁定接口/字段/状态及文件所有权后派发：
- Coding（/root/wp03_coding）：新增backend/app/support_requests.py；修改auth.py、results.py、main.py；cleanup.py只允许request_delete早返回前同事务关闭请求/推进版本及purge新请求外键的最小集成，不改物理Worker/清理状态。
- Testing（/root/wp03_testing）：新增backend/tests/cr_wp03_fixtures.py、test_cr_support_requests.py、test_cr_support_content.py、test_cr_support_http.py；仅必要更新test_auth.py、test_privacy_content.py、test_results.py中的旧purpose断言/用例，保留原测试目标，不编写业务代码。
- 定向Security（/root/wp03_security）：独立只读审授权/范围/并发/流/旧旁路与错误/日志，报告给Main并交Coding修复，不并写文件。
- Main：维护计划与docs/CR-WP03_VALIDATION_REPORT.md，冻结契约、审核/派发修复和隔离资源，不承担常规业务编码。

全部SQL迁移001～010、models/tasks/queue/processing/Worker/deletion_control、前端/真实配置/依赖及WP02测试默认只读；新模块和独立测试属已批准WP03计划，cleanup最小连接由用户本轮明确授权。需额外文件先提出具体理由并等批准。

专用PG17 tmpfs/随机回环端口、UUID schema/合成账户/HTTP子进程，仅合成存储替身，不读真实.env或调用模型；最终回收。新API与ASGI/真实socket HTTP及DB证据分别标注，不能把模拟存储或本包测试当WP04物理删除/WP07真实切换通过。旧purpose路由/授权helper拒绝，真实旧部署/凭据/连接M3不在本包。

WP03交接结果（2026-11-05）：56项新增+236项既有后端测试，共292 passed、0 failed、0 skipped（102.80秒）；独立Security定点复核关闭，Main指纹核验白名单及资源回收。首次Testing因工具额度中断后由/root/wp03_testing_resume接续同一独立职责/文件所有权，无并写。主机媒体缺失和runner多余参数问题已在现成Linux测试镜像内纠正并完整复验，未改配置/源码或削弱断言。最终[WP03验证报告](CR-WP03_VALIDATION_REPORT.md)区分真实PG/ASGI/TCP与合成存储、未执行的物理删除/恢复等边界。Main提交验收建议；负责人验收及下一包授权前停止，未启动WP04/M3/S3-11/12。

## 10. WP04启动与冻结（2026-11-05）

用户正式验收WP03并单独授权WP04。Main只读核查implementation及现有未提交内容，冻结118份起点指纹；011尚未占用，001～010冻结。具体数据/映射、生产SH/EX/anchor、受控维护及可写文件见[删除实施冻结](../../CR-WP01_DELETION_OPERATIONS.md#7-wp04实施冻结2026-11-05技术落实)。编码与独立测试分文件并行，定向Security只读；每次派发含九项交接信息。资源仅专用PG17/私有合成S3/真实Linux双容器与FFmpeg，标签WP04、无业务卷/用户内容/计费API；最终回收。真实业务011/M3、历史BACKUP批准、WP05与后续不随本包授权。

WP04交接结果（2026-11-05）：333项后端（292既有+41新增core）零失败/零跳过，148.58秒；另6种真实Linux物理配置通过，共339个不同用例，不累计诊断。Security S01～S06与Main事务/重放等修复已定点关闭；Main复核源码指纹、资源标签并回收专用PG/S3，所有本包容器/命名卷/网络均无残留。Coding仅12份源码/迁移，Testing仅4份新测试/夹具及6份必要旧适配；文件责任/证据见[WP04验证报告](CR-WP04_VALIDATION_REPORT.md)。011尚未在真实库应用，001～010/S3-10证据/配置/前端不变。Main建议验收，未代负责人作验收决定，未启动WP05/M3/后续阶段。

## 11. WP05启动、前端契约与文件责任（2026-11-05）

WP04已获负责人正式验收，用户单独授权WP05；WP03/04后端契约、001～011及历史证据只读，未授权WP06/07/M3。Main冻结126份起点指纹，不整理既有未提交内容。

### 11.1 实际白名单

- Coding：修改frontend/src/App.tsx、TaskPanel.tsx、AdminPanel.tsx、ResultPanel.tsx、style.css；删除旧ConsentPanel.tsx及DeletionPanel.tsx专属功能代码；新增SupportRequestPanel.tsx、SupportQueuePanel.tsx、support.ts。所有React内部props由Coding在此范围整合，不改后端/依赖/config。
- Testing：仅frontend/tests/ui-smoke.mjs及必要新增frontend/tests/wp05-fixtures.mjs，拥有独立浏览器/合成API及其夹具；不改业务组件，不以删断言适配失败。
- Main：本计划与docs/CR-WP05_VALIDATION_REPORT.md，维护共享接口/状态/派发/缺陷与证据。默认Main/Coding/Testing，无固定第四安全角色；出现重大安全风险再提出专项。
- 其他文件只读；新增文件/跨范围需求先Main核理由/归属，不自行扩包。前端build产生dist为可重建产物，不改真实部署或保存用户内容。

### 11.2 API映射（后端冻结）

所有路径经/api同源代理，credentials=same-origin/cache=no-store；POST/DELETE带现有X-CSRF-Token。请求编号不是凭证，无purpose/consents/旧admin任务内容/删除历史/人工清理调用。

| UI操作 | 冻结API |
| --- | --- |
| 普通用户查看当前 | GET /tasks/{id}/support-request，读取request或null、support_version、server_time。 |
| 创建/再生 | POST /tasks/{id}/support-requests，operation/scope/expected_version；仅REGENERATE另含expected_request_id。 |
| 撤销 | DELETE /tasks/{task}/support-requests/{精确ID}，204后GET当前，无旧ID追新请求。 |
| 管理员独立队列 | GET /admin/support-requests?limit=20&cursor=...，保持server升序，游标内存栈上下页，不缓存历史有效性。 |
| 编号精确定位 | GET /admin/support-requests/{UUIDv4}，元数据入口与列表进入同一精确请求展示，不按task找新请求。 |
| 删除管理员请求 | DELETE /admin/support-requests/{id}，204撤权，不删源任务；立即清本地对应内容后刷新。 |
| 所有者USER/ADMIN | GET /tasks/{task}/result或export?language=zh/en、/video；管理员元数据仅user_id=当前自己可直接查看。 |
| 其他用户内容 | GET /admin/support-requests/{精确ID}/result或export?language=zh/en、/video；按scope提供操作，后端逐次裁决。 |
| 删除视频 | DELETE /tasks/{task}，202且{id,task_id,accepted:true}仅接受；隐藏任务/相关内容，提示后台清理，绝不称已物理清完。 |

summary=中英文摘要及JSON，不视频；video=仅视频，无摘要/JSON；both=两者。管理员自己的任务隐藏排障申请，不调用普通用户support API。不能通过旧purpose/URL/仅UUID取得他人内容。

### 11.3 状态与并发边界

- 请求UI状态为读取中/已确认无有效请求/有效Meta+版本+server时钟锚/过期或不可用/操作中/状态未知；初始或未知时不能提交CREATE/REGENERATE。expected_version必须已确认且JS安全整数，scope为summary/video/both。
- 同步busy ref阻双击；每次显式用户操作捕获一次前置版本/ID，409或网络结果未知只GET恢复并说明，不自动换版本重发旧POST。GET失败保持未知，成功后仅下一次明确用户操作可提交。DELETE精确ID后GET若出现不同新请求，不声称已撤销新请求。
- 请求/页面/会话以AbortController及操作序号隔离；校验在response/json/blob每次await后。旧GET不能覆盖更新version，旧请求/旧账号/旧language/旧export响应不能重新显示或触发下载；登出、会话/角色变化及卸载清内存和媒体。
- 24h绝对到期展示取server expires_at，倒计时使用server_time+performance.now单调锚（可保守计网络耗时），不能靠设备Date.now延长；本地到期撤下内容并重新查询，不自动生成。有效/到期最终由后端实时裁决，已送达内容不虚称可远程抹除。
- 管理员列表及选中请求定期/焦点刷新，按服务端顺序及精确ID；失效/404/403清相应内容，无owner或新ID回退。cursor编码并保存在内存，刷新/分页不偷偷读取内容。
- 视频使用受保护同源native src，保留后端Range/分块复核，不整片Blob缓存或公开/长期链接。关闭/到期/失效/切账号停止并移除src；摘要与JSON拒绝时不继续显示旧结果，下载需当前有效操作且及时revoke objectURL。
- 不使用localStorage/sessionStorage/IndexedDB/service worker保存编号/正文/令牌，不输出控制台内容或回显原始错误。编号复制仅显式用户操作，错误以固定文案分类，401走会话失效，403/404/409/429/503不自行推断已获授权。
- 保留登录/导入、配额/FIFO/五状态进度、正常重试/取消与双语结果/导出；不改变原性能口径。WP05只移除旧产品界面，后台安全保障已在WP04保留。

### 11.4 验收与独立证据

AC17～20、CR-AC04/05/06/08/09/11～18/21/23，E2E14/19，T26/27/29及CR测试计划§5/6。实际构建React+独立Chrome/Edge临时profile+专用随机回环合成HTTP API，验证：
- 普通用户三scope/UUID/24h/复制/撤销/再生；管理员own无请求、他人请求列表分页/定位/删除与scope控件/JSON路径。
- 409/重复点击/响应丢失恢复仅GET、陈旧version、迟到GET/POST/export/旧会话不覆盖；设备时钟偏移、到期/403/404/401失效及媒体停止。
- 所有修改CSRF/同源会话，UUID非凭证；无旧purpose/删除历史/人工调用或UI；202文案仅接受，不声称完成。
- 登出/换账号清内容与异步响应、无浏览器持久存储或敏感日志；原五状态/正常重试取消/额度/双语/无效结果不可导出。
测试Agent从契约独立编写Mock，不以编码自检代替；真实浏览器+合成API不等同真实后端授权/视频解码/S3/模型或WP06闭环，报告分别标注。只用合成身份/内容，不读真实.env/Key/用户媒体，结束回收服务器/浏览器及专属profile，不全局清理。每包完成停等负责人验收，不自动启动WP06。

WP05交接结果（2026-11-05）：Coding完成5改/3增/2删的10项前端源责任，Testing仅2份独立浏览器资产；npm run test:ui构建成功、22组真实Chrome+合成API场景通过，源码前后指纹一致，专用HTTP/浏览器/profile已回收。T01面板key与Main导航/详情代次/元数据刷新问题已修并独立复测；后端/001～011/依赖配置未改。详见[WP05验证报告](CR-WP05_VALIDATION_REPORT.md)，明确剪贴板/下载探针、异步接缝与未测真实视频/后端闭环。Main建议负责人验收，未自动认定通过或启动WP06/07/S3-11/12。

## 12. WP06启动冻结（2026-11-05）

WP05已获负责人正式验收，用户单独授权WP06。后端app、frontend/src、001～011及WP02～05证据保持指纹，不擅自修业务实现/覆盖已应用SQL；发现缺陷由Main定位，实质产品/架构冲突停相关部分。WP07真实M3/旧部署/恢复开放不执行。

- Coding仅新增backend/tools/wp06_environment.py：专用合成环境start/status/stop/seed及同源静态UI→真实API代理；必要helper同文件，不写其他源码/迁移/真实配置。生命周期与状态记录在专属TEMP目录，不存真实秘密。
- Testing仅新增backend/tests/test_cr_wp06_integration.py、frontend/tests/wp06-real.mjs；可执行现有只读后端/前端/物理/迁移测试。独立测试/故障/负载、JSON证据写docs/baseline/cr-wp06.json，不修改旧断言换通过。需要额外测试资产先Main核边界。
- Main仅本计划、docs/CR-WP06_VALIDATION_REPORT.md、docs/CR-WP06_MANUAL_ACCEPTANCE.md、docs/CR-001_TEST_PLAN.md必要追踪补充。默认Main/Coding/Testing，无固定安全第四角色。
- 资源全部专用标签CR-WP06/synthetic_only，PG *_test、私有RustFS、网络/命名主数据卷/控制卷、两个Worker及API、Node/浏览器临时profile。不使用现有业务compose/.env/真实Key或媒体；MODEL_CALLS_ENABLED=false/offline。必要PG/S3主卷用于真实stop/start恢复，不是新内容备份/快照。
- 环境接口：environment.py start --state PATH（可默认专用TEMP）、status --state PATH、stop --state PATH；JSON状态记录仅本包资源名/端口/合成账户/样本路径及可证明身份。stop逐资源校验标签/精确路径，无全局prune。serve使用同源回环端口代理/api保留Cookie/CSRF/Range/no-store；不改冻结API。
- 自动化结束清测试临时资源；按用户要求明确保留一套本地合成手工验收环境，报告实际URL/账号/样本/启动停止步骤，不将“保留”误写为全部清零。停机/故障注入不得作用此后端以外资源。
- 验证需新跑001～011/兼容约束、完整非模型后端及现有前端Mock、真实Chrome+真实HTTP/API/Worker/PG/S3闭环；上传/五状态/双语/JSON/video decode与seek/Range、owner/3scope/期限/撤销/并发替换/列表负载、N>=2/FIFO/配额/重试删除隔离、故障与health/status/告警/受控retry。24h/90天可合成近边界与受控时间/PG数据接缝，不宣称实际等待24h/90天。
- CR-AC01～24、T25～35逐项通过/失败/未执行，细分运行层/接缝；不能用旧通过代替本次验证，不把模拟网络/模型称真实供应商。WP07恢复门禁及M3只记保留/未执行，不越权。没有足够证据的项目不填通过。
- 每次派发九项信息、文件写权不重叠；共享环境接口冻结后并行。测试失败给Main指派Coding限工具修复，业务缺陷不得测试绕过。完成提交报告及简洁手工清单，负责人实际人工验收不自动记通过，停等验收不启动WP07。

WP06例外授权：真实集成发现W06-B01，负责人已明确批准backend/app/cleanup_ops.py唯一一处参数化SQL的LIKE 'S3%'改为LIKE 'S3%%'及独立复测。只修psycopg占位解析，SQL匹配/权限/迁移语义不变，其他业务源继续冻结；由Coding执行，Testing证明真实CONFIG成功与审计回滚。新指纹及原覆盖缺口只记WP06，不改旧验收证据。

WP06交接结果（2026-11-05）：修后333后端/152.22秒、42项010结构、6种Linux物理配置、22组Mock浏览器、8项真实浏览器联调及整组10项真实HTTP/Worker/PG/RustFS集成通过；分层重叠不合计。W06-B01唯一源码例外为获批SQL百分号转义，成功retry与精确23514审计COMMIT回滚独立验证。自动化环境精确回收，另保留干净合成手工环境，见[本包报告](CR-WP06_VALIDATION_REPORT.md)及[人工指南](CR-WP06_MANUAL_ACCEPTANCE.md)。用户人工结果尚未填写，Main只建议验收，不认定通过，不执行WP07/M3/S3-11/12。
