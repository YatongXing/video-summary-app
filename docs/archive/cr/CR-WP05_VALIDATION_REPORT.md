# CR-WP05：前端排障请求与访问交互验证报告

**日期：** 2026-11-05  
**依据：** 用户正式验收WP04并单独授权WP05；已验收的WP01 L1.0、WP03/04报告，以及[本包冻结](CR-001_IMPLEMENTATION_PLAN.md#11-wp05启动前端契约与文件责任2026-11-05)。  
**状态：** 实现、独立浏览器验证及测试资源回收完成，提交负责人验收；**未自动认定已验收，未启动WP06**。  
**起点：** implementation，HEAD `96b093afed1ef0cb36685ba46325d52c805d612c`，126份已有可见文件指纹；原未提交变更原位保留，无Git提交/推送/整理。

## 1. 实际文件与职责

| 责任 | 文件 | 结果 |
| --- | --- | --- |
| Coding | [App.tsx](../../../frontend/src/App.tsx) | 会话/角色/账号代次、旧auth/me防回填及内容组件重置。 |
| Coding | [TaskPanel.tsx](../../../frontend/src/TaskPanel.tsx) | 普通用户排障入口、管理员无自申请、202仅接受；保留五状态/额度/重试/取消。 |
| Coding | [AdminPanel.tsx](../../../frontend/src/AdminPanel.tsx) | 元数据与独立待处理请求页面导航、编号定位、owner直读；正常刷新保留reader，权限失败清除。 |
| Coding | [ResultPanel.tsx](../../../frontend/src/ResultPanel.tsx) | owner/精确request路径、summary/video/both显示、双语/JSON、旧异步与媒体清理。 |
| Coding | [style.css](../../../frontend/src/style.css) | 请求/队列必要样式，移除旧专属样式；保留原主题。 |
| Coding，新增 | `frontend/src/SupportRequestPanel.tsx`、`frontend/src/SupportQueuePanel.tsx`、`frontend/src/support.ts` | 请求CRUD、版本/时间锚、管理员分页/精确定位及共享类型/校验。 |
| Coding，删除 | `frontend/src/ConsentPanel.tsx`、`frontend/src/DeletionPanel.tsx` | 删除旧purpose/删除记录/人工产品专属代码，未移除后台安全保障。 |
| Testing | [ui-smoke.mjs](../../../frontend/tests/ui-smoke.mjs)；新增[wp05-fixtures.mjs](../../../frontend/tests/wp05-fixtures.mjs) | 独立浏览器场景及合成HTTP/故障/异步接缝、资源生命周期。 |
| Main | 本报告及[实施计划](CR-001_IMPLEMENTATION_PLAN.md)§11 | 共享映射、状态/白名单冻结、缺陷指派、源码/证据审核与交接。 |

共14项文件交付：7修改、5新增、2授权删除。ImportForm/main.tsx、前端依赖/锁文件/配置以及全部后端/001～011保持本包起点；未创建真实业务迁移、部署变更或模型调用。

## 2. 冻结API映射与前端行为

路径均经/api同源访问，fetch credentials=same-origin、cache=no-store；修改请求保留X-CSRF-Token，不把UUID当密码或登录凭证。

| 入口 | API/前端语义 |
| --- | --- |
| 普通用户当前请求 | GET /tasks/{task}/support-request；只有已确认snapshot/version后才允许提交，展示当前UUID、实际scope、到期时间。 |
| CREATE / REGENERATE | POST /tasks/{task}/support-requests，scope/expected_version；仅REGENERATE带expected_request_id。同步busy防双击，成功/409/响应不确定后只GET核对，不自动换版本重放。 |
| 撤销 | DELETE /tasks/{task}/support-requests/{精确旧ID}，然后GET真实状态；不因旧请求操作误称新请求已撤销。 |
| 管理员有效队列 | GET /admin/support-requests，保留服务端升序/limit20/游标，内存页栈前后页；编号直接显示/显式复制，无历史页面。 |
| 精确查找/列表进入 | GET /admin/support-requests/{UUIDv4}，两个入口共用精确详情；不按任务追新ID，不自动读正文。 |
| 管理员删请求 | DELETE /admin/support-requests/{id}；确认说明撤权但不删除原视频，先清本地相应内容并刷新列表。 |
| USER/ADMIN所有者 | /tasks/{task}/result或export?language=zh/en、/video；管理员任务元数据仅当前user_id对应行有直接内容操作，无自申请。 |
| 管理员其他用户内容 | /admin/support-requests/{精确ID}/result或export?language=zh/en、/video。summary仅摘要/JSON，video仅视频，both可分别查看；草稿scope不改变实际授权。 |
| 视频删除 | DELETE /tasks/{task}，确认202及accepted/task_id后隐藏任务和相关内容；提示请求已接受、后台清理且尚未确认实际完成。 |

无consents/purpose/旧admin任务内容、deletions/cleanup-jobs调用、页面或入口。后端仍逐次判断会话、归属、scope、24h和删除状态；UI隐藏/显示按钮不是授权依据。

## 3. 状态、失效及隐私边界

- 普通请求状态区分未确认、已确认无当前请求、有效Meta/版本/时间锚、失效及操作中。网络/409只GET恢复，GET失败保持未知，下一次明确操作才能新提交；不保留排障generation receipt。
- 原任务重试的request_key仍按旧规则复用，取消排障回执不影响普通视频重试幂等、额度/FIFO/并发或性能目标。
- 绝对期限来自expires_at，剩余时间取server_time及performance.now单调锚并保守计网络耗时；设备Date.now偏移不能续期。本地到期移除有效控件/媒体，最终权限看服务端即时响应，不自动再生。
- 请求/页/语言/会话边界以取消信号与代次保护，response/json/blob每次await后校验。旧GET不能覆盖新版本，旧POST、auth/me或导出不能跨账号回填或触发下载。
- 403/404/失效清相应内容，元数据403也清旧行；无owner/新ID回退。正常同页成功元数据刷新保留合法reader，不把每15秒强行关闭内容当安全方案。
- 视频native同源受保护src保留后端Range及分块复核，不整片Blob缓存；关闭/失效/切账号停止并移除src。JSON仅显式操作下载，旧blob结果丢弃，临时objectURL及时revoke。
- 编号/正文/CSRF不写localStorage、sessionStorage、IndexedDB或service worker；无敏感控制台输出/原始错误回显。现有认证Cookie机制保持；复制仅显式用户动作，已经发送/下载的副本无法远程抹除。

## 4. 独立测试与证据

**最终22个浏览器场景组全部通过，npm run test:ui exit 0。** 实际构建React运行于真实Chrome156.0.8078.81，Node24.22.1；TypeScript/Vite通过，最后Vite构建281ms。UI总耗时未单独计时，不给伪精确值。独立测试纳入指纹记录的10份现存源码运行前后SHA一致（8份有效修改/新增及2份受保护源码）；Main另对全部起点文件核验，见§6。

| 场景组 | 关键oracle |
| --- | --- |
| 1～3 | 五状态/进度/额度，owner中英文摘要/JSON，三scope/UUID/创建/再生/精确撤销/24h/复制。 |
| 4～7 | 409只GET、草稿scope不改授权；真实HTTP201 headers后断正文恢复；设备时钟偏移不改变授权期限；到期撤有效控件。 |
| 8～11 | 迟到GET json不压新版本；登出后blob不下载；跨账号迟到POST不刷新旧内容；旧auth/me JSON不替换新登录。 |
| 12～16 | 管理员owner直读/无自授权、游标分页、编号/scope路径；403清精确内容；删除期间不能错误导航/迟到详情不能复开；删请求未发源任务DELETE。 |
| 17～20 | 同页元数据刷新保持owner reader，403清旧行/内容；无效输出禁导出；原retry request_key、取消、导入授权提示保留。 |
| 21～22 | 202仅接受，旧API/UI无调用，无持久存储/敏感console/alert；修改请求有CSRF。 |

复现：使用已有本地依赖及支持CDP的Chrome/Edge，所有身份/内容/HTTP响应为合成，无真实Key或媒体。

```powershell
cd E:\GitHub\video-summary-app\frontend
npm run test:ui
```

测试用Node随机回环HTTP同时提供built dist与合成API。**这是实际浏览器+TCP合成API，不是本包重新验证真实后端认证/授权。** JSON/blob/迟到POST及auth/me的特定交错使用前端响应方法/Promise接缝；POST201后断正文使用真实HTTP中断。剪贴板与下载click用显式探针，未证明真实系统剪贴板权限或磁盘下载。未测真实视频解码/seek、真实S3/Worker联调、真实模型/质量/新性能或WP06完整闭环；未生成截图，不声称额外视觉验收。

前期17/20组是中间候选，不累加。发现的DOM等待测试自身问题在测试侧修正；业务缺陷保留原反例，未通过改挑最后一个面板或删断言掩盖。

## 5. 缺陷、前端安全复核与交接

| 项 | 修复及独立关闭依据 |
| --- | --- |
| W05-T01 | TaskPanel相邻授权/结果相同key导致多面板累积；独立稳定support-/result- key，原三scope循环反例通过。 |
| M05-QUEUE | 删除中切页使旧finally调用旧cursor list覆盖新页；busy期间禁止本页导航/查找/刷新并同步guard，独立交错通过。 |
| M05-INVALIDATE | 失效详情可能被早期locate响应重开；统一关闭推进详情代次，先close再捕获新定位代次，403/404不追新ID。 |
| M05-METADATA | GET/json后先核取消信号，权限失败清旧行/内容；上下文变化才重置，成功同页刷新保留reader，避免修复引入定时强关。 |

本包按已批准默认Main/Coding/Testing三角色执行，没有固定第四安全Agent。上表为Main源码复核与Testing独立浏览器反例，不冒称独立安全Agent审查；后端WP03/04既有安全实现完全未改。未发现仍需Coding修复的本包测试失败或后端契约冲突。

Main先冻结§11接口/状态/文件责任后派发；Coding仅10项前端源责任（5修改/3新增/2删除），基本构建自检后暂停；Testing独占2个测试文件，从AC/E2E独立设Mock和场景；缺陷经Main→Coding→Testing。各派发具任务ID、输入版本、文件、冻结契约、必做/禁做、AC/E2E/T、证据、依赖/交接九项，未并写同文件。Agent完成不等于产品验收。

## 6. 资源、Git与文件完整性

最终测试finally核验输出：serverClosed=true、browserExited=true、profileRemoved=true。只回收本包随机端口服务器、headless子进程及边界校验后的专属video-summary-wp05-*临时profile，没有清其他浏览器资料。未建立PG/S3/模型资源或读真实.env；前端未发现.env文件，未改任何实际配置。

最终以126份WP05起点文件逐字节核对：117份不变，7份授权修改、2份授权删除，新增5份，共14项交付；无白名单外变更。全部后端、001～011、配置/依赖、原S3-10及WP02～04证据保持原指纹；上表实际交付与§1一致。

| 实现/测试资产 | SHA-256 |
| --- | --- |
| frontend/src/App.tsx | `ef7bc66bd453314c0de33fa17e5d6a248efef0995f8e0bfe8c33b9ea306729ac` |
| frontend/src/TaskPanel.tsx | `5da7ad6297ba53b7456db02992c0da08bf5b7c09169f666ab64b4acdc7a9ca3f` |
| frontend/src/AdminPanel.tsx | `9eaccc957941b89fe0c3b90d78374cbf62b5c2379d14c2c13a8f4f0502c40331` |
| frontend/src/ResultPanel.tsx | `adc036efc49e053d650685a9525347692a23e50c39a990092ee2bc421533eec9` |
| frontend/src/style.css | `b01b40165f81309335da4ade30ac8deb995bf1dbb2ed9929b04ba4bbc8f362ee` |
| frontend/src/SupportRequestPanel.tsx | `8b5a9b240e91bc4691c0fbf9fcb60b3c0ab153294530e80960b30fba3039d9a7` |
| frontend/src/SupportQueuePanel.tsx | `4c1550f12f028d74209b347e85f201ad948ca4c0281bc3893d3f5031868af736` |
| frontend/src/support.ts | `a8b4aff3061db12e02e5599dd275cd75b52df7b569acf7823b26560e1dff5879` |
| frontend/tests/ui-smoke.mjs | `a56ca470a51f6a26d6e6709ea2dee5cd38851c04c9ac0ed3526776f5d098be9f` |
| frontend/tests/wp05-fixtures.mjs | `5ed3a8606df36f138cc2d065a7cca365e41a8dbccfe515ad1b69f41d030a513d` |

最终22个本地Markdown链接及适用锚点检查通过，新增文件空白/EOF检查通过，git diff --check exit 0（仅换行提示）。8份前端实现与2份独立测试资产SHA与最终验证交接完全一致。

没有reset/clean/stash/checkout/commit/push。后续若另授Git保存，按14项实际白名单审阅，排除dist/node_modules、凭据、媒体、卷、日志和缓存；不自动保存或推送。

## 7. 退出与WP06准入

**Main建议验收WP05；尚未代产品负责人认定验收通过。** 前端范围和22组隔离浏览器证明完成，旧产品功能已移除，未发现新的实质接口冲突。已下载副本、真实剪贴板权限和真实视频播放等限制见上，不将合成API当真实服务的安全证据。

WP06须负责人先验收本包并另行授权，冻结WP02～05代码/迁移指纹后，执行前后端/真实HTTP+Worker+私有合成S3闭环、负载/故障/迁移恢复预演及负责人本地人工验收。真实M3、旧凭据/会话授权终止、maintenance/恢复开放仍仅WP07专项；S3-11/12另授，本轮未推进。
