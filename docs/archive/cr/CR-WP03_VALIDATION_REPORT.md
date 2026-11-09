# CR-WP03：排障请求 API 与实时内容授权验证报告

**日期：** 2026-11-05（实施始于11月4日）  
**依据：** 已验收的 WP01 L1.0、L-01～L-05 / R-01～R-07，以及用户本轮 WP03 独立启动授权。  
**状态：** 实现、独立验证及定向安全复核完成，提交产品负责人验收；**未自动认定已验收，未启动WP04**。  
**Git 起点：** implementation，HEAD `96b093afed1ef0cb36685ba46325d52c805d612c`；原未提交文档与历史删除保留，不暂存、提交或推送。

## 1. 实际文件与责任

| 责任 | 实际文件 | 内容 |
| --- | --- | --- |
| Coding | [support_requests.py](../../../backend/app/support_requests.py)，新增 | 请求 CRUD、版本 CAS、有效列表/编号定位、限速、共享实时鉴权与固定无内容审计。 |
| Coding | [auth.py](../../../backend/app/auth.py) | 会话使用数据库实时时间；旧 purpose 路由/helper 拒绝，不再签发旧授权。 |
| Coding | [results.py](../../../backend/app/results.py) | owner 直读与管理员精确请求内容路径；摘要/导出最终复核，视频 HEAD/打开对象后及每块读前读后复核。 |
| Coding | [main.py](../../../backend/app/main.py) | 注册路由；新 API 的一致错误、输入脱敏、依赖故障拒绝及 no-store。 |
| Coding | [cleanup.py](../../../backend/app/cleanup.py) | 仅 request_delete 早返回前关闭请求/推进版本/审计；既有清理时先删除请求外键关联。未重构物理清理。 |
| Testing | [cr_wp03_fixtures.py](../../../backend/tests/cr_wp03_fixtures.py)、`backend/tests/test_cr_support_requests.py`、`backend/tests/test_cr_support_content.py`、`backend/tests/test_cr_support_http.py`，新增 | 专用真实 PG、ASGI、独立 TCP HTTP、并发/故障/范围/流验证。 |
| Testing | [test_auth.py](../../../backend/tests/test_auth.py)、[test_privacy_content.py](../../../backend/tests/test_privacy_content.py) | 旧 purpose 用例适配新请求；保留 CSRF、跨用户、元数据脱敏及 ALLOWED/DENIED 审计目标。test_results.py 未改。 |
| Main | 本报告与[实施计划](CR-001_IMPLEMENTATION_PLAN.md)§9 | 白名单、派发/交接、审核与证据。 |

无其他业务文件写权。001～010、模型/任务/队列/Worker/processing/deletion_control、前端、依赖与真实配置保持只读；没有 011、物理清理改造或业务数据库操作。

## 2. 已实现 API 与权限

| API | 语义 |
| --- | --- |
| GET /tasks/{task_id}/support-request | 普通用户所有者读取有效请求或 null、support_version、server_time；已到期不依赖归档立即无效。 |
| POST /tasks/{task_id}/support-requests | CREATE/REGENERATE；必填 scope、expected_version，REGENERATE 必填 expected_request_id，CREATE 不接受替换 ID。成功 201；冲突 409 后 GET。 |
| DELETE /tasks/{task_id}/support-requests/{request_id} | 精确旧 ID 撤销，重复/旧 ID 不误撤新请求、不重复推进版本。 |
| GET /admin/support-requests | 真实管理员有效待处理列表，创建时间/UUID 稳定升序，游标分页，只有非内容必要元数据。 |
| GET /admin/support-requests/{request_id} | 精确编号定位，不追随替换后的新请求。 |
| DELETE /admin/support-requests/{request_id} | 同事务关闭请求、推进版本及审计；幂等，不删除原任务/结果。 |
| GET /tasks/{task_id}/result、export、video | USER/ADMIN 自己任务走所有者路径，无需支持请求；管理员身份不授予他人内容权限。 |
| GET /admin/support-requests/{request_id}/result、export、video | 当前真实管理员会话、精确请求/任务/源用户、范围、期限及删除状态共同授权。result/export 支持 zh/en。 |

权限矩阵：summary 允许中英文摘要、约定 JSON/导出，不允许视频；video 只允许视频；both 两者均允许。管理员自己的任务不创建排障请求。

旧 /tasks/{id}/consents 的 GET/POST/DELETE，以及 /admin/tasks/{id}/result|export|video 和旧 purpose helper，认证后返回 410；修改入口仍校验 CSRF。新内容入口不接受 purpose/code 作为授权。UUIDv4 只是定位符，不是免登录凭证。

错误：401 会话，403 角色/CSRF，404 无权或请求/任务不可用，409 版本/当前请求/结果状态，422 输入/游标，416 仅授权后的 Range，429 带 Retry-After，503 数据库/删除控制依赖。新请求 API 返回 error.code/message，不回显正文、密码、SQL 或堆栈。

## 3. 事务、时间与失效边界

- 写入按来源用户→task→精确请求的顺序加锁。tasks.support_version 前置检查、关闭旧行、新 UUID/期限、版本 +1 及审计在同事务提交；数据库唯一约束兜底。无独立回执、加密/nonce/key 或 epoch 结构。
- CREATE 不替换仍有效请求；过期旧行先关闭再插入。REGENERATE 只替换前置版本与 ID 均匹配的有效请求。关闭精确行才推进版本，旧 DELETE 不能作用于新 ID。
- 锁后读取 clock_timestamp，规范到 UTC 后加 86400 秒；内容联合查询使用同一数据库真实时间核验会话/授权/任务保留期，不使用事务开始 now 或前端时钟。提交后重新确认当前状态；不能恢复历史 POST 原始响应。
- 响应丢失可 GET 当前真实结果；重复/迟到 POST 返回 409。客户端不得自动刷新版本重发旧 CREATE，这一 UI 行为留 WP05 验证。
- owner 与管理员内容检查分别使用单条联合查询读取当前身份/会话、任务及授权事实，再检查独立删除控制；不把多次旧读取拼成授权，也不跨网络 I/O 持行锁。
- 请求关闭、替换、到期、任务删除或保留期结束，后续新读取被拒绝；源账户/管理员会话或角色失效同样拒绝。
- 视频每 256 KiB 读前与读后、yield 前复核；对象 HEAD 后及打开流后也复核，避免失效后返回 Range 元数据。失效则停止后续块并关闭流，已经交给网络的数据无法撤回；最终检查先于撤销的当块仍属于批准的在途边界。测试不声称撤回客户端已有副本。
- 一般限速沿用 R-07：查找/列表分别 60 次/管理员/分钟及 300 次/来源/分钟；列表默认20/max100；创建成功5次/task/滚动5分钟。查找/列表独立提交计数，404不退还额度，不引入猜密码惩罚。
- request_delete 在已有 job 早返回前，同一 SQL 事务关闭相关请求/版本/90天无内容事件；原独立删除意图仍先持久化。物理停止、清理完成、四态重试及运维能力留 WP04，不以本包 404 当作清理完成。

## 4. 独立运行证据

最终有效结果：**292 passed / 0 failed / 0 skipped，102.80秒，exit 0**。其中 **56项新增WP03测试 + 236项既有测试**；WP02三份独立文件未混跑，历史42项不再累计。

| 运行 | 结果与用途 |
| --- | --- |
| 最终Linux完整回归 | 上述292项；已有 `video-summary-app-test:latest`（镜像ID `220689d0be48`），当前app/tests/tools只读挂载，含真实FFmpeg/ffprobe，真实专用PG17.11。 |
| 原主机诊断 | 256 passed / 1 failed / 34 skipped，170.80秒；缺FFmpeg/ffprobe导致旧非媒体上传预期4xx变503及媒体跳过。未改源码/弱化断言，该组已在最终Linux运行中实际补齐。 |
| Linux启动参数诊断 | 290 passed / 2 failed，104.13秒；runner额外全局指定未初始化journal影响两个旧用例。移除不属原test target的额外参数后完整重跑；WP03/清理专测仍自行启用并初始化journal，不以禁用专测换通过。 |

前期47项、定点复测及上述诊断均不重复累加。最终56项包括16请求/事务、39内容/授权、1真实TCP闭环。没有仍失败或skip项目；不表示所有未来规格已执行，浏览器、真正丢包和DST特定服务运行用例尚未执行。DB真实到期/跨锁及86400秒约束已验证，DST相关结构证据仍为WP02历史，后续完整时区回归须保留。

证据映射采用[有效测试计划](CR-001_TEST_PLAN.md)与[数据/API契约](CR-WP01_DATA_API_DESIGN.md)，不覆盖原 S3-10 报告：

| 用户验收要求/规格 | 证据与边界 |
| --- | --- |
| 角色、归属、停用/旧会话；CR-AC06/07/23 | ASGI+真实PG，独立 TCP HTTP 的登录会话鉴权；管理员 owner 内容路径、其他用户拒绝。 |
| 三scope/双语/JSON/视频；CR-AC13 | 独立参数矩阵；对象存储明确为合成适配器，非真实 S3。 |
| 创建/替换/原子失效；CR-AC08/09/19/20、T26 | 真实PG并发与任务行锁等待；第三观察者检查未提交状态；201/409与唯一有效行。 |
| 响应丢失/重复/旧版本；T26 | 提交成功后重复POST/GET核对实际状态、撤销后迟到POST409；覆盖客户端提交结果不确定的恢复语义，未注入真实丢包或证明浏览器重试行为。 |
| 原子回滚 | 专用 deferred constraint trigger 在真实PG COMMIT拒绝，验证请求/版本/审计整体回滚，不以只mock函数代替。 |
| 撤销/替换/管理员删除/源任务删除；CR-AC15/16/18/21 | 请求API及删除最小连接；原内容保持、已有job早返回仍关闭新请求。物理删除未在本包证明。 |
| 24h/DB时钟/源30天；CR-AC14、T27 | 真实DB近到期、跨锁到期及保留期；正常期限不因重复提交延长。测试不以后台到期整理作为授权条件。 |
| Range及流中失效 | 真实TCP正常Range/撤销后404；读后yield前及HEAD期间失效为ASGI+真实PG+合成存储回调，非真实存储/网络在途竞争证明。 |
| 旧purpose旁路、CSRF、限速/脱敏；T28/29 | 新旧入口交叉、无权/未知目标、畸形游标及独立计数；允许/拒绝审计保留。真实旧部署失权留WP07。 |
| 任务/登录/配额/队列/S3-10兼容 | 本包最终非模型后端回归；不作为新性能基线、真实模型质量或WP04/07通过。 |

## 5. 定向安全复核及问题关闭

/root/wp03_security 独立只读审源码/契约，未编写实现、未借 Coding 自检代替审核。结论：没有未关闭的 WP03 安全阻塞项，属于静态审查。

| 问题 | 修复与关闭依据 |
| --- | --- |
| W03-S01：非法游标元素可触发未处理异常 | 两元素均必须字符串，畸形游标422；独立测试保留对象/数字反例。 |
| W03-S02：分次读取授权事实可能混用过期快照 | owner/foreign 单条联合读取当前请求、角色、会话、任务及DB时间；列表/定位最终复用；安全Agent复核，运行矩阵/失效测试验证。 |
| T03-AUDIT：拒绝审计随外层异常回滚 | 已知请求内容入口在独立只读路径的独立事务持久化 DENIED；固定无内容分类，不伪造未知目标，不按流块重复审计。安全Agent复核锁序；旧用例恢复 ALLOWED/DENIED 及精确访问次数。 |

鉴权/审计依赖不可用时拒绝，不降级使用 purpose。安全审查没有运行数据库/HTTP/存储；实际证据由独立 Testing 提供。WP04 物理停止与 WP07 恢复/正式权限仍是后续门槛。

## 6. Agent 派发与交接

Main 派发前冻结 WP01 L1.0 字段/三scope/状态、API/错误及256KiB边界、任务锁序、审计/旧路由退出、文件所有权和隔离资源。各任务均含ID、输入版本、模块路径、冻结契约、必做、禁做、AC/T映射、证据和依赖/交接九项。

| 子任务 | 文件/职责与输入 | 交接 |
| --- | --- | --- |
| WP03-Coding /root/wp03_coding | L1.0契约、A/B设计、删除连接/切换边界、PRD/AC与计划；仅§1五份源码。 | 提交实现/说明/指纹与py_compile；Main指派三项定点修复，Testing独立验证，不写测试/配置/迁移。 |
| WP03-Testing /root/wp03_testing → /root/wp03_testing_resume | 独立读AC/E2E、CR测试规格及§7反例；仅四份新增测试/夹具与两份必要旧用例适配。 | 前一角色因工具额度中断后，由独立Testing续接同一文件所有权；恢复者未改测试，使用现成Linux测试镜像补齐环境。独立PG/ASGI/TCP、锁等待/提交故障和旧回归，问题交Main，不改Coding文件。 |
| WP03-Security /root/wp03_security | 同一有效契约及源码；只读鉴权、旧旁路、删除、流、审计与恢复边界。 | 两项安全修复及审计风险定点复核关闭；无文件写入、无DB操作。 |
| Main /root | 计划/报告、白名单、资源、源码/测试审核与跨角色缺陷分派。 | 整合独立证据、范围/指纹/回收验证；不以Agent完成自动认定产品验收。 |

Testing遇到的pytest夹具注册与合成过期会话时间约束问题均在测试侧修正，不计为通过或删除断言。无多人同时写同一文件，无额外业务设计或跨包实施。

## 7. 环境隔离、资源与复现

专用 `vsa-wp03-mw2yie2m`，标签 `vsa.work_package=CR-WP03` / `vsa.synthetic_only=true`；PostgreSQL17.11、tmpfs、随机回环端口49367、wp03_test库、UUID schema、合成身份/任务/摘要/字节，无业务卷。没有读取真实.env/密钥/Cookie或用户视频，没有真实模型请求；HTTP子进程仅传入最小合成环境，模型调用关闭。

最终runner `vsa-wp03-tests-resume` 已 --rm 回收；HTTP子进程由夹具finally终止，容器临时文件随之回收。Testing核验PG非系统/非public schema为空、其他DB连接数0。Main再核实专用PG两个标签后只停止 `vsa-wp03-mw2yie2m`；--rm/tmpfs回收完成，按精确名称复查无该容器。没有停止业务容器或删除业务卷。

早先主机pytest临时诊断目录未做全局清理，不声称主机所有临时资料均已删除；所用资料均为合成数据。未建立新的内容备份。

复现需新建同等隔离资源并显式注入 TEST_DATABASE_URL、WP03_ISOLATED_INSTANCE、WP03_TEST_PORT；不回退业务 DATABASE_URL，不在现有业务环境运行：

实际最终执行环境为已有Linux test镜像，app/tests/tools只读覆盖当前文件，工作目录/srv；共享专用PG网络，因此容器内使用127.0.0.1:5432、WP03_TEST_PORT=5432，非主机49367。显式MODEL_CALLS_ENABLED=false、PYTHONPATH=/srv，不传真实.env；不额外全局设置journal，专项夹具自行初始化。

```text
python -m pytest tests -q -rs --tb=short
  -o cache_dir=/tmp/wp03-pytest-cache
  --ignore=tests/test_cr_support_schema.py
  --ignore=tests/test_cr_migrations.py
  --ignore=tests/test_cr_roles.py
```

以上分行展示一次命令的参数，实际同一命令执行；主机缺媒体工具的初轮不是最终复现环境。

WP02的三份独立测试要求其专用实例/库身份，未伪造WP02环境变量或改其夹具来混跑；本包不重新累计其42项历史通过。旧 test_migrations.py 仍在本次全回归内。

## 8. 文件完整性与 Git

以 WP03 开始时112个已存在的可见文件为只读指纹基线（不含真实.env及此前已删除文件）：7个允许文件修改，105个逐字节不变；新增6个允许文件，共13个本包交付文件，无基线文件丢失、无白名单外变更。最终逐文件核验已确认上述范围与数量。

001～010的SQL资产均与WP03起点一致；010 SHA-256为 `df119b7fbf4150c480f7bef96c559d777e74082bef6c1de31d3b80ba9cd05d47`。旧校验历史见[WP02报告](CR-WP02_VALIDATION_REPORT.md)。原 `docs/REGRESSION_BASELINE.md`、`docs/baseline/local-baseline.json`、local_baseline.py、Docker/compose、依赖、配置模板、前端及其余受保护源码不变；不将旧S3-10结果当新需求通过。

| 实现/测试资产 | SHA-256 |
| --- | --- |
| backend/app/support_requests.py | `bba8a1a7eaa46a7db7e5fb60bcfc0cb662cfecb33e11c82c16dbd608c34a6da2` |
| backend/app/auth.py | `7a44634d0c86bdcc41dd52550e01dfe819fe81cc028864c150c80a5fdcd2c967` |
| backend/app/results.py | `b229a0076673dcc002ba706815a12f9f5a27948eb6504a18ddb5089b20c3d4f5` |
| backend/app/main.py | `f38ca513e91957ac31a87213503123a3ad3695ee90192e4c595d602614646220` |
| backend/app/cleanup.py | `50d7c5d6797449cf799df3540875966fdd180e6c7572768a77391d2336efbb9f` |
| backend/tests/cr_wp03_fixtures.py | `a9be3dcfab44f1bc162d0f5450416193ff9a078a00039ad62793bad97752af1f` |
| backend/tests/test_cr_support_requests.py | `6d764648feb78a1a60a0291ef87011b6f4e7dfaa081a0fcb7642dffb4cb71459` |
| backend/tests/test_cr_support_content.py | `3e28facb098e312db1b77f2d61832911e98ff860d8f469805016818f0c3d420b` |
| backend/tests/test_cr_support_http.py | `9fda2451f5981bc1b0cee138892f10c7390e37dd885141ea1e185c32905f6f52` |
| backend/tests/test_auth.py | `9daf3e3eb9a16443a33c23f85dce6735c6d5f27781277ccee0ac5524f2a3c06a` |
| backend/tests/test_privacy_content.py | `54805b3246bfe525814b0f6727a6cc6ed636700d5e1d2e1626d7bf8b662beb00` |

最终文档/格式验证：本报告与更新计划共22个本地Markdown链接及适用锚点检查通过；新增文件空白/EOF检查通过；git diff --check exit 0。Git仅有既有LF→CRLF提示，无格式错误。实现/测试11份指纹与独立交付完全一致；112份起点文件核验确认105份不变、7份均在授权范围，无额外删除。

不把本包授权修改的 auth/results/main/cleanup 称为“S3-10源码完全未改”；准确结论是只有白名单源码和必要测试适配发生变化，原S3-10证据与其他受保护文件保持。工作区已有历史文档改动/删除没有整理、覆盖或恢复。

Git建议：负责人另行授权保存时，按本报告实际文件白名单审阅并暂存；排除.env、凭据、媒体、数据库卷、日志和缓存，再由负责人决定提交/推送。本轮未执行Git保存，也不建立含用户内容备份。

## 9. 剩余边界与 WP04 准入

**Main结论：WP03实现、独立隔离回归与定向安全审查已完成，满足本工作包提交验收的条件，建议产品负责人验收；尚未由负责人正式验收。** 没有待Coding修复的本包业务失败或安全阻塞。此结论限于本报告明确执行的后端范围，不替代WP06完整跨模块/负载与WP07实环境证据，也不对未执行用例盖章。WP04尚未启动，须负责人另行授权。

- WP03交付后旧前端仍可能调用410的purpose入口；UI改造属于WP05，不能据此直接部署开放新业务。
- 本包仅在合成专用实例应用冻结迁移供测试；未对业务DB执行010/M3、停止旧部署/连接或切换真实运行凭据。新源码拒绝旧API不等于真实旧binary已失权。
- 视频与导出已给出批准的最终检查边界，不能撤回已发送/下载副本；真实S3网络故障、负载、浏览器及跨模块闭环留WP06，真实模型/新性能评测未执行。
- WP04须独立授权，先冻结R-04四态/单job_version具体字段与映射，并重新核对011编号；保持视频全局N、每普通用户1、FIFO/配额/退款/重试与30天/90天规则。
- WP04必须真实Linux Docker/FFmpeg验证取消+task SH/EX候选：父死子活、FD/inode、并发N>=2与清理互斥/不误删；失败停止相关实施另审替代。清理故障发现/退避/维护status-health-retry、BACKUP审批边界及定向安全均不能由本包替代。
- WP07真实切换、旧会话/授权终止、维护/恢复门禁及最低故障发现实际运行后方可开放，另行授权。WP03完成后停止，不自动推进任何后续包。
