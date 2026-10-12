# GDUE DevPortal

[中文](#中文) · [English](#english)

## 中文

DevPortal 是运行在**使用者自己电脑**上的游戏构建下载器：浏览器只打开本机页面，Python 客户端从 DevHub 读取获授权的构建列表，并直接从百度网盘下载文件。支持断点续传和 SHA-256 校验；DevHub 不转发文件。

**当前状态（2026-10-13）：**正在准备 Windows x64 客户端预览版，但 DevHub 的 Portal 服务端、设备密钥及独立设备访问尚未部署。下面的步骤需要管理员先完成服务端准备，不能直接用于公开互联网。

### 部署与使用

**管理员先准备服务端：**在私有 GameDevUE 项目中评审并部署包含 Portal 接口的 DevHub 镜像，先做停写备份并保留回滚镜像；安全配置百度授权、登记构建，向可信使用者**单独**签发设备密钥。为使用者设备提供经验证的 HTTPS 地址，或在该设备上单独建立私有隧道。不要把 DevHub 的 `8765` 端口直接开放到公网；本仓库不包含服务端或凭据。

**Windows 便携版（无需安装 Python）：**发布后，从 [v0.1.0-rc.1 预览版](https://github.com/bayzhen/DevPortal/releases/tag/v0.1.0-rc.1)下载 `DevPortal-windows-x64.zip` 和 `SHA256SUMS.txt`，核对 ZIP 的 SHA-256，解压并保留整个 `DevPortal` 文件夹。在该文件夹的上一级打开 PowerShell：

```powershell
.\DevPortal\DevPortal.exe configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN
.\DevPortal\DevPortal.exe serve
```

配置时按隐藏提示输入管理员签发的设备密钥，然后在**同一台电脑**打开终端打印的本机地址。预览版**未签名**，服务端未上线前不能实际下载。

**从源码运行（Python 3.11+，Linux/macOS）：**在此仓库根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m dev_portal configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN
.venv/bin/python -m dev_portal serve
```

`configure` 会隐藏密钥输入；`serve` 只监听本机并打印访问地址。可用 `serve --downloads PATH` 指定下载位置，默认 `~/Downloads/DevPortal`；Windows 下载目录必须位于用户配置文件内。只有在**使用者自己的电脑**上建好授权隧道时，才可把服务端地址改为 `http://127.0.0.1:PORT`；别人的 `127.0.0.1` 无法直接连接你的电脑。

**安全提示：**不要把设备密钥写进命令、网页、截图或仓库。百度授权令牌可能拥有较广的应用目录权限，密钥只应发给可信使用者。下载到 Windows 的 Linux 游戏包仍不能在 Windows 运行。客户端不会自动更新；本项目尚未指定开源许可证。

本地离线测试：`python -m unittest discover -s tests -v`。

## English

DevPortal is a build downloader that runs on the **recipient's own computer**. The browser opens a local page; the Python client lists authorized builds through DevHub and downloads files **directly from Baidu**. It supports resume and SHA-256 verification. DevHub does not relay the file bytes.

**Status (2026-10-13):** A Windows x64 client preview is being prepared, but the Portal backend, per-device keys, and independent-device access are **not deployed yet**. The steps below require the operator to prepare the server first; they do not create a public service.

### Deploy and use

**Server operator first:** In the private GameDevUE project, review and deploy a Portal-enabled DevHub image after a stopped-writer backup, keeping a rollback image. Configure Baidu authorization privately, register builds, and issue a **separate** Portal key to each trusted recipient. Provide a verified HTTPS origin or a private tunnel set up on that recipient's device. Never expose DevHub port `8765` directly to the internet. This public repository contains neither the backend nor credentials.

**Windows portable (no Python installation):** Once published, download `DevPortal-windows-x64.zip` and `SHA256SUMS.txt` from the [v0.1.0-rc.1 preview](https://github.com/bayzhen/DevPortal/releases/tag/v0.1.0-rc.1). Check the ZIP's SHA-256, then extract and keep the whole `DevPortal` folder. Open PowerShell in its parent directory:

```powershell
.\DevPortal\DevPortal.exe configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN
.\DevPortal\DevPortal.exe serve
```

Enter the operator-issued key at the hidden prompt, then open the printed local URL **on that same computer**. This preview is **unsigned** and cannot download real builds until the backend is deployed.

**Run from source (Python 3.11+, Linux/macOS):** From this repository's root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m dev_portal configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN
.venv/bin/python -m dev_portal serve
```

`configure` prompts for the key without echoing it. `serve` binds only to loopback and prints the local URL. Use `serve --downloads PATH` to choose a destination (default: `~/Downloads/DevPortal`); on Windows it must be inside the user profile. Use `http://127.0.0.1:PORT` only if an authorized tunnel is set up **on the recipient's own device**. Someone else's `127.0.0.1` cannot reach your computer.

**Safety:** Never put the Portal key in a command, web page, screenshot, or repository. A delivered Baidu owner token may carry broad app-directory access; share keys only with trusted recipients. A Linux game archive downloaded on Windows is still a Linux build. There is no automatic client update or selected open-source license.

Offline tests: `python -m unittest discover -s tests -v`.
