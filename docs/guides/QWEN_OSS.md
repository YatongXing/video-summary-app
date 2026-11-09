# 使用真实千问处理视频

## 本机使用

1. 在项目根目录的 `.env` 中填写千问 AI 平台按量 `DASHSCOPE_API_KEY`。保留现有模型和端点。
2. 在项目根目录运行更新脚本。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\deploy\Update-VsaLocal.ps1
~~~

3. 更新成功并显示 `Worker: qwen` 后，打开[本机网页](http://127.0.0.1:58080/)，登录后上传视频并查看双语结果。

应用接收不超过 2 GB（2,000,000,000 字节）、最长 2 小时的 MP4/MOV，视频需为 H.264，音轨可为 AAC 或 MP3。原文件不超过 7,499,988 字节时，Worker 使用 Base64 内联传输。更大的文件须启用下方 OSS 配置。每次处理尝试向千问发送完整视频和音轨。超时后先核对千问控制台用量，再决定是否手动重试。Worker 的模型请求等待时间为 600 秒，不自动重发。[千问视频输入文档](https://platform.qianwenai.com/docs/developer-guides/multimodal/vision) · [Qwen3.8-Omni-Flash 上限](https://help.aliyun.com/zh/model-studio/qwen-omni)

## 启用 2 GB 大文件处理

1. 在阿里云 OSS 创建专用标准存储桶，访问权限设为私有并开启阻止公共访问。选择千问服务能够访问的公网 OSS 地域。版本控制须从未启用，同时关闭保留策略和复制功能。
2. 为 `vsa-qwen/` 前缀启用对象最后修改后 1 天删除的生命周期规则，使用前缀匹配，无额外筛选条件。应用在传输或模型处理结束后会立即尝试删除临时副本。未确认删除的对象会登记并在 Worker 恢复后继续处理。OSS 生命周期是异步兜底。[OSS 生命周期](https://help.aliyun.com/zh/oss/user-guide/overview-54/)
3. 使用专用 RAM 访问密钥。桶级权限限于 `oss:GetBucketAcl`、`oss:GetBucketPublicAccessBlock`、`oss:GetBucketVersioning`、`oss:GetBucketLifecycle`，对象权限限于该桶 `vsa-qwen/*` 的 `oss:PutObject`、`oss:GetObject`、`oss:DeleteObject`。[OSS 授权语法与权限](https://www.alibabacloud.com/help/en/oss/user-guide/authorization-syntax-and-elements)
4. 在根目录 `.env` 新增下面四项。地域使用地域 ID，杭州示例如下。自行填入桶名和密钥，避免把 `.env` 放入 Git 或发送到聊天。

~~~dotenv
QWEN_OSS_REGION=cn-hangzhou
QWEN_OSS_BUCKET=
QWEN_OSS_ACCESS_KEY_ID=
QWEN_OSS_ACCESS_KEY_SECRET=
~~~

5. 再运行上面的更新脚本。更新器会核对 Worker 的模型和 OSS 配置。首次上传超过内联上限的合成视频时，核对双语摘要和 OSS 控制台中临时对象的清理结果。

大文件会完整上传到专用 OSS 桶。应用只向千问发送约 15 分钟有效的 HTTPS 签名读取链接。链接有效期间，持有者可以读取该临时对象。OSS 传输和千问模型可能分别产生费用。删除临时对象不会撤回千问已经读取的数据。[OSS 签名读取链接](https://help.aliyun.com/zh/oss/user-guide/how-to-obtain-the-url-of-a-single-object-or-the-urls-of-multiple-objects/) · [OSS 计费](https://help.aliyun.com/zh/oss/billing-overview/)

更新入口和就绪检查见[本机运维](../../deploy/README.md)。
