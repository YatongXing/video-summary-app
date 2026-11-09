# CR-002 人工验收调整：独立测试报告

日期：2026-11-06。范围仅为用户新增授权的两项调整：结果展开后不重复显示Task ID；管理员搜索允许任意输入并在清空时自动恢复默认列表与第一页。沿用Main冻结的trim规则，前后空白忽略、空串/全空白为默认列表；其他输入按字面包含、不视为LIKE通配。

## 最终结果

| 验证 | 结果 | 实际范围 |
| --- | --- | --- |
| 定向后端 | **58 passed / 0 failed / 0 skipped，102.06秒** | test_cr002.py与test_auth.py；仅本次专用PG17、UUID schema、合成身份。新增10种参数：1字符、非hex、中文、%、_、引号、反斜线、120字符、混合通配字符、SQL注入样式文本；全部200且结果严格等于字面包含。完整UUID优先/大小写/跨页/白名单、角色/旧旁路/owner/动态复核、限速与2秒查询期限等原安全断言保留。 |
| Mock实际Chrome | **14组通过** | 实际build；USER/ADMIN展开结果后每任务卡片仅1组编号/复制；任意输入传后台；30条合成元数据，搜索第2页清空自动回默认第1页且无task_id参数、不点搜索；旧搜索response.json在收头后被人为延迟，清空后再释放仍不覆盖默认结果；原异步清理/页脚/个人内容和metadata隔离保留。 |
| 实际后端只读Chrome | **3组通过，Chrome156.0.8078.81** | 只打开原始明确合成USER1/ADMIN种子，验证展开后仅1组编号和准确复制；管理员完整UUID与1字符/Unicode/%/_/引号/长输入查询成功，清空自动返回默认列表。未运行旧wp06-real上传/seed/delete流程。 |

## 文件与覆盖调整

- `backend/tests/test_cr002.py`：移除旧最短8hex/格式/36字符拒绝预期，保留limit、offset、旧user_id参数保护；新增10种字面匹配参数用例。
- `backend/tests/test_cr_wp06_integration.py`：旧短查询422预期更新为200；本轮未运行该整套真实故障集成。
- `frontend/tests/ui-smoke.mjs`：替换重复结果编号与短查询拒绝断言，增加任意输入、清空分页及迟到响应覆盖。
- `frontend/tests/wp05-fixtures.mjs`：管理员Mock按limit/offset分页，支持真实验证清空页码。
- `frontend/tests/wp06-real.mjs`：旧结果页重复编号断言改为整卡片只有1组；本轮未执行此含上传流程的脚本。
- 新增 `frontend/tests/acceptance-readonly.mjs`：固定已确认合成种子，只读现有人工环境，不创建或删除任务。

没有删除测试文件、没有降低仍有效的安全断言。对应前后SHA-256及原始新结果保存在 `baseline/cr-002-acceptance-testing.json`。上一轮CR-002/WP06历史报告、JSON、manifest保持原位，不以本轮58项替换上一轮312项系统回归证据。

## 人工环境保护与资源

人工环境保持 `vsa-wp06-172ff1a984`、UI http://127.0.0.1:50658、API http://127.0.0.1:50657，state `<验收临时目录>\state.json`。

安全定点复核通过后，先核state身份、5容器标签和Id/StartedAt，再仅重启 `vsa-wp06-172ff1a984-api` 一次加载新auth代码。五个容器Id保持，其他四个容器StartedAt保持，未停止/重建/seed/清理人工环境，未连接该人工数据库。用户验收期间新增任务完全保留。

真实浏览器仅打开固定原合成任务：USER1 `8b10e001-fe46-4e4c-a984-9d71556aee4f`，ADMIN `8ee10940-c841-4c94-8822-e648ef500442`。只读全部元数据可用，没有自动选择最新任务或未知媒体正文。

本次专用PG `vsa-cr002-acceptance-4f14b787` 已核 `CR-002-ACCEPTANCE`/synthetic 标签后删除，标签容器残留为空；临时连接描述文件删除。Mock服务器、两个Chrome进程及专用profiles已回收。人工环境仍保留运行，等待用户继续验收。

本轮没有修改业务源码、SQL迁移或部署配置；没有Git、真实密钥/.env、计费模型、正式迁移、WP07或其他生产操作。没有为两项局部调整重复运行Linux物理删除、迁移或故障全回归。
