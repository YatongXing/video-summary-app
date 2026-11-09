# CR-002 独立 Testing 交付

日期：2026-11-06。依据已批准 CR-002 最终契约与 C2-01..14 测试规格。实际代码为当前文件，不引用 Git HEAD 作为最终版本。人工验收待负责人，WP07 未启动。

## 最终实跑结果

| 层 | 最终结果 | 证据边界 |
| --- | --- | --- |
| 完整非模型后端 | 312 passed，0 failed，0 skipped，153.08秒 | 当前实现，真实 PG17，Linux FFmpeg/ffprobe，专用私有 RustFS，两角色/任务/队列/配额/重试/删除/故障/迁移全回归 |
| 010 专项 | 42 passed，14.84秒 | 临时010-only软件副本，保留原WP02隔离守卫与001..009校验，非011整链的替代；完整迁移链由core涵盖 |
| Linux物理 | 6 passed，36.83秒 | 真实FFmpeg/ffprobe、父死子活、FD/inode、SH/EX、全局物理互斥、N>=2删A保B、DB断连不释放物理锁 |
| 真实HTTP集成 | 7 passed，51.95秒 | API+双Worker+PG+RustFS闭环、双角色双语JSON/Range、PG/S3实际停启、Worker停启排队不扣额恢复扣一次、实际对象删除、TCP背压下owner删除停流、CONFIG告警/维护retry/SQLSTATE23514 COMMIT回滚、旧授权永久退役/搜索/配置 |
| Mock浏览器 | 12组 passed | 实际build和Chrome，API为Mock；五态真实ID逐个复制、双语JSON、迟到Blob登出不下载、管理员独立场景、搜索/页脚/移动端，不声称真实后端 |
| 真实浏览器 | 7组 passed，Chrome156.0.8078.81 | USER/ADMIN分别正常上传30秒视频、Worker完成、双语摘要、MP4解码播放和seek、回执/列表/结果真实ID一致与复制、metadata全页无个人内容/视频、大小写完整/部分搜索、页脚与移动端 |

各层目的不同且存在交叠，不相加成一个总测试数。浏览器seek：USER currentTime=5.37643、ADMIN=5.36158，duration均30秒、readyState均4。真实双语JSON与Range精确字节由HTTP验证。模型为offline FakeModel，不证明真实语义质量、供应商费用、延迟或生产容量。

## C2追踪与安全替代

新增 `test_cr002.py` 37项包含在312项内：USER/ADMIN本人允许、其他owner拒绝；12种HEAD/read×角色变化/会话撤销/停用/会话到期/删除/保留期失效；8种独立journal身份丢失/损坏/不匹配/目录丢失的读前及流中拒读；摘要/导出最终角色复核；三种历史scope均无跨用户授权；退休API认证/角色/CSRF；已有job早返回仍关闭历史请求；搜索完整/部分大小写/非法/长度/limit/offset/无匹配；共享8hex前缀25任务跨三页稳定且无重复；顶层及嵌套字段白名单；actor60和source300跨管理员独立限速且不串登录；真实PG表锁触发2秒安全超时；邮箱空/非法/合法/CRLF。

C2-01..03：core和真实HTTP/浏览器；C2-04..06：新增搜索/白名单/限速及全局分页；C2-07..08：Mock五状态、真实回执/任务/结果与双角色及页脚；C2-09..13：完整后端/010/Linux/真实故障维护；C2-14：历史指纹保留及已回收自动环境、新鲜人工环境。

完全依赖旧授权成功语义的请求创建/再生/三scope/24h授权/UUID队列专测已被退休拒绝和owner动态复核替代；010结构、历史关闭不可逆/外键/版本保护、历史清退与恢复测试保留。不会为了通过率删除仍有效安全断言。

## 测试文件变化

### 删除

- `backend/tests/test_cr_support_requests.py`
- `backend/tests/test_cr_support_content.py`
- `backend/tests/test_cr_support_http.py`

### 修改

- `backend/tests/test_auth.py`
- `backend/tests/test_privacy_content.py`
- `backend/tests/cr_wp03_fixtures.py`
- `backend/tests/test_cr_wp06_integration.py`
- `frontend/tests/ui-smoke.mjs`
- `frontend/tests/wp05-fixtures.mjs`
- `frontend/tests/wp06-real.mjs`

### 新增

- `backend/tests/test_cr002.py`

### 逐字节保留

- `backend/tests/test_health.py`
- `backend/tests/test_migrations.py`
- `backend/tests/test_worker.py`
- `backend/tests/test_ingest.py`
- `backend/tests/test_media.py`
- `backend/tests/test_safe_download.py`
- `backend/tests/test_tasks.py`
- `backend/tests/test_queue.py`
- `backend/tests/test_worker_queue.py`
- `backend/tests/test_preprocess.py`
- `backend/tests/test_model_adapter.py`
- `backend/tests/test_processing.py`
- `backend/tests/test_results.py`
- `backend/tests/test_cleanup.py`
- `backend/tests/test_deletion_control.py`
- `backend/tests/test_system_failures.py`
- `backend/tests/cr_wp02_fixtures.py`
- `backend/tests/test_cr_support_schema.py`
- `backend/tests/test_cr_migrations.py`
- `backend/tests/test_cr_roles.py`
- `backend/tests/cr_wp04_fixtures.py`
- `backend/tests/test_cr_cleanup_schema.py`
- `backend/tests/test_cr_cleanup_operations.py`
- `backend/tests/test_cr_cleanup_linux.py`

`test_auth.py` 将旧创建成功预期改为410，并保留认证、CSRF、元数据脱敏与跨owner拒绝。`test_privacy_content.py` 保留任务删除与离线恢复撤销，旧管理员grant流中撤销由新增更广owner动态复核替代。`cr_wp03_fixtures.py` 移除无人使用的create helper，保留历史种子和兼容辅助。`test_cr_wp06_integration.py` 保留通用任务/配额/故障/对象删除/维护回滚，将跨用户grant流改为owner删除停流，增加CR002退役/搜索/配置。前端fixture/UI和真实浏览器不再发送已退役产品请求。

`backend/tools/wp06_environment.py` 仅新增显式运行环境SUPPORT_EMAIL传递，默认空；`cr002_validation.py` 提供仅接受显式、验证过标签资源的core/Linux、010和真实HTTP复现入口，读取TEST资源不加载.env。当前工具最终编辑仅重用原测试参数，已语法检查。

## 过程失败及处置

首轮core306通过/2旧契约断言失败/2缺S3跳过；按新退休语义改写断言并补专用S3资源。第二轮310通过/2S3连接失败来自专用tmpfs权限导致RustFS退出，仅修正合成容器UID0，最终完整312零失败零跳过。Linux首轮4通过/2未注入显式合成DSN，补runner参数后6通过。新增helper首次测试上下文缺失、派生DSN密码隐藏，以及重复文件名选中旧卡片均属测试诊断，已修复并保留完整断言。

自动审批曾拒绝在替代覆盖前删除旧测试；先新增并获得24项等价安全通过证据后才清理。锁测试初次方案因ambient DATABASE_URL被拒，改为显式TEST_DATABASE_URL并核验同一活*_test连接与UUID schema后获准。没有访问业务库，没有执行被拒动作。

## 环境与退出

旧手工环境 vsa-wp06-b44855a096 已先保存无内容资源/标签证据后精确回收。自动环境及CR002专用PG/S3、010容器/软件副本、Linux临时配置、Mock服务与Chrome profile均已回收。CR-002标签容器/卷/网络残留均为空；没有全局prune，没有删除其他工作包或业务资源。

新版人工环境另建并保留：

- UI：http://127.0.0.1:50658
- API：http://127.0.0.1:50657
- State：`<验收临时目录>\state.json`
- 资源：`vsa-wp06-172ff1a984`，五个运行容器、三个主数据/控制卷、一个专用网络、一个同源代理。
- 合成账户：user1@wp06.invalid、user2@wp06.invalid、admin@wp06.invalid；统一密码 `WP06-synthetic-only!`。各通过正常API准备一份3秒成功任务，非模型真实语义。
- 3秒/30秒媒体位置、任务ID、标签、运行状态及清理证据见 `baseline/cr-002-testing.json`；用户指定邮箱仅注入人工运行配置。
- 清理：调用 `backend/.venv/Scripts/python.exe backend/tools/wp06_environment.py stop --state <上述完整state路径>`，工具核验身份/标签后只回收该环境；不要全局清理Docker。

桌面/移动端截图 `baseline/cr-002-desktop.png`、`baseline/cr-002-mobile.png` 为最终独立metadata场景、合成邮箱配置，不含真实用户数据。未读真实.env/密钥、未真实模型计费、未Git、未正式迁移/M3/WP07/S3-11/S3-12。自动化完成后停止，人工验收尚待负责人。
