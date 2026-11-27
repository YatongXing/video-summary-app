# 依赖安装与自检

以下步骤适用于 Windows 和 PowerShell。先按路线准备工具，再回到[从这里开始](GETTING_STARTED.md)创建实例。

| 工具 | 从源码部署 | Docker 部署 | 用途 |
| --- | --- | --- | --- |
| Docker Desktop（Linux 容器）和 Compose v2 | 必需 | 必需 | 构建或加载镜像，运行 Web、API、Worker、PostgreSQL 与对象存储。 |
| Edge 或 Chrome | 使用网页；源码浏览器验收也需要 | 使用网页 | 登录、上传和查看结果。Windows 自带的 Edge 即可。 |
| Node.js 22.12 或更高的 22.x，含 npm | 在宿主机构建前端时需要 | 容器构建或使用预构建镜像时可省略 | 按锁文件安装前端依赖，将 TypeScript 和 React 编译成静态文件。 |
| Python 3.11 | 运行源码隔离验收脚本时需要 | 可省略 | 在宿主机编排构建和验收；API 与 Worker 的 Python 由镜像提供。 |
| Git | 用 Git 克隆时需要 | 用 Git 克隆时需要 | 下载 Release 源码 ZIP 并解压可省略。 |

## 1. 安装 Docker Desktop，确认 Linux 引擎可用

1. 检查电脑是否符合 [Docker Desktop 的 Windows 系统要求](https://docs.docker.com/desktop/setup/install/windows-install/)。使用 WSL 2 后端时，在 PowerShell 运行下面的检查。如果系统尚未安装 WSL，在管理员 PowerShell 执行 `wsl --install`，按提示重启；若提示内核过旧，执行 `wsl --update`。[微软 WSL 安装说明](https://learn.microsoft.com/windows/wsl/install)
2. 从 Docker 官方页面下载安装 Docker Desktop，安装时选择 WSL 2 后端。启动 Docker Desktop，等引擎显示运行中，再新开一个普通 PowerShell 窗口。
3. 依次执行以下命令。最后两项应显示 Linux 服务端和 Compose 版本。后续命令使用同一个 `desktop-linux` 上下文。

~~~powershell
wsl --status
docker context ls
docker --context desktop-linux info --format '{{.OSType}}'
docker compose version
~~~

如果找不到 `docker`，重新打开终端。如果没有 `desktop-linux` 或无法连接引擎，先启动 Docker Desktop，确认使用 Linux containers，并在设置中检查 WSL 2 引擎。看到 `linux` 后再继续。首次下载镜像和依赖需要联网；若公司网络要求代理，请配置 Docker Desktop 的代理，并检查终端访问 npm 源的权限。

## 2. 按路线安装开发工具

**Node.js 与 npm。** 在官网下载页面选择 Windows 安装包和 22.x 系列中的 22.12 或更新版本，保留 npm 和 PATH 选项。安装后重开终端运行以下两条命令。若 PowerShell 阻止执行 `npm.ps1`，直接运行 `npm.cmd` 即可。[Node.js 官方下载](https://nodejs.org/en/download)

~~~powershell
node --version
npm.cmd --version
~~~

**Python 3.11。** 若要运行 README 中的隔离验收，从 Python 官网安装 Windows 的 Python Install Manager，在新终端运行 `py install 3.11`，再用下面命令确认。已安装旧版 Python Launcher 且能选中 3.11 时，可直接使用。脚本自行构建容器，宿主机无需预装 FastAPI 或数据库驱动。[Python 官方 Windows 下载](https://www.python.org/downloads/windows/)

~~~powershell
py -3.11 --version
~~~

**Git 与浏览器。** 选择克隆源码时，安装 Git for Windows 后在新终端运行 `git --version`。选择 ZIP 时，只需解压到可写目录，打开含 `README.md`、`backend`、`deploy` 和 `frontend` 的那一层。自动浏览器验收默认寻找 Chrome 或 Edge；若安装在自定义位置，设置 `$env:UI_TEST_BROWSER` 为实际的浏览器可执行文件绝对路径。[Git for Windows](https://git-scm.com/downloads/win)

## 3. 确认服务依赖和模型账户

| 依赖 | 谁来提供 |
| --- | --- |
| PostgreSQL 17、RustFS 1.0.4 | Compose 使用配方指定的镜像并管理数据卷。宿主机无需另装数据库或对象存储。 |
| Python 运行库、FFmpeg 和 FFprobe | 由应用镜像安装，Worker 从容器内读取视频信息。安装在宿主机上的 FFmpeg 不会替代容器中的工具。 |
| 模型账户 | 离线流程无需密钥。要获取真实摘要，需要能调用项目所用模型的千问服务账户、Worker 专用的 API Key 和出站连接；服务可能计费。 |
| 阿里云 OSS | 使用 Qwen 处理超过 7,499,988 字节的本地原片时，还需专用私有桶、最小权限凭据及生命周期规则。本地 RustFS 负责应用自己的文件存储。详见[OSS 配置](guides/QWEN_OSS.md#启用-2-gb-大文件处理)。 |

准备好后，打开[从这里开始](GETTING_STARTED.md)。在新窗口继续操作时，先回到源码根目录；部署过程中设置的 PowerShell 变量只在当前窗口生效。
