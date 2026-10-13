# GDUE DevPortal

[中文](#中文) · [English](#english)

## 中文

DevPortal 是运行在**使用者自己的 Windows 电脑**上的游戏构建下载器。应用中的 Python 工作进程建立经过 SSH 服务器公钥校验的本机私有隧道；浏览器只打开本机项目页面，绝不接收配对码或百度下载授权。客户端从百度网盘直接断点续传，并核对文件大小和 SHA-256；DevHub 不转发文件。

**重要：暂停 v0.2.0-rc.1 的 Windows 下载。** 已发现该预览版使用了 Windows 文本模式处理二进制分段，可能破坏归档并造成 `Partial file changed before verification`。这是**客户端缺陷，不是个人码失效**；关闭应用，先在本机私密备份 `%USERPROFILE%\Downloads\DevPortal\.devportal\`（含隐藏 `.part` 和 `.json`），不要反复重试或删除断点，等待修复版经真实 Windows 测试后再下载。旧版 ZIP 不会自动更新。私有 Portal 服务端和个人 tester 码已在管理员侧验证，使用者本人完整下载仍须另行验收。历史版 [v0.1.0-rc.1](https://github.com/bayzhen/DevPortal/releases/tag/v0.1.0-rc.1) **不支持**下面的一码登录。

### 使用者：只需三步（已修复的新版发布并由管理员开通后）

1. 待管理员确认修复版通过 Windows 测试后，从 [DevPortal Releases](https://github.com/bayzhen/DevPortal/releases)下载该**修复版** Windows x64 ZIP 与 `SHA256SUMS.txt`（不要使用有已知问题的 v0.2.0-rc.1），检查 SHA-256，解压整个 `DevPortal` 文件夹到**新目录**；保留里面所有文件。便携版未签名，Windows 可能显示来源确认。
2. 双击 `DevPortal.exe`，**只在原生窗口**粘贴管理员私下发送的**本人专属长配对码**。可选勾选“在这台电脑记住登录”（默认不勾选），点击“登录并打开项目”。不要在浏览器里粘贴配对码或手动输入服务器地址/SSH 命令。
3. 在自动打开的本机项目页选择获授权的构建并下载。项目页会标明目标平台；Linux 构建即使下载到 Windows 也不能在 Windows 运行。下载进行时不要退出应用；网络中断后重新登录并重试，可按验证规则续传已保存的部分。

此流程仍要求使用者的 Windows 账户中**已经有管理员授权的 SSH 身份**和系统 OpenSSH 客户端；客户端为安全起见不读取用户的任意 SSH 配置/ProxyCommand，因此身份须已在系统 SSH agent 中可用，或位于 OpenSSH 默认密钥路径（如 `%USERPROFILE%\.ssh\id_ed25519`）。仅靠 SSH 别名里配置的自定义 `IdentityFile` 不会被自动加载；管理员应在交付前完成受控配置，使用者无需自己敲 SSH 命令。配对码不含 SSH 私钥、管理员密码或百度令牌。若 SSH 身份不存在、服务器指纹改变或项目暂不可用，请联系管理员，**不要**关闭主机身份检查或改用明文公网 HTTP。“忘记登录”删除本机记住的码；不勾选记住不会写入可复用设备码。Windows 记住登录由当前 Windows 账户的 DPAPI 加密，不能移植到另一台电脑。配对码是可复用的 bearer 凭据，不是一次性六位验证码，必须通过可信私有渠道传递。

**恢复边界（仅适用于未来经 Windows 验收的修复版）：**后台按“固定 SSH 身份核对 → DevHub 项目目录 → 每次下载刷新授权 → 百度分段 → 私有 checkpoint → 完整大小/SHA-256 → 不覆盖发布”的顺序处理。断线后在原生窗口重新登录，在项目页点 `Download / resume`；页面偶发进度读取失败会重试，也可点“刷新”。首次 checkpoint 前留下的**空** `.part` 可重新开始；不明来源的非空 `.part`、损坏或身份不符的断点会保留供管理员私下核查。已经发布的文件只有经独立大小/SHA-256 核对才会被识别为完成；不会仅凭文件名覆盖或信任它。“忘记登录”不会删除下载断点。旧 rc.1 的已损坏前缀不能据此宣称可恢复，先备份再联系管理员。修复代码/托管 Windows CI 与真实 Windows 全量下载是不同门槛，目前**没有新的 Windows Release**。

**管理员先准备：**在私有项目里以经过评审的窄范围镜像部署 Portal 服务端，先停写备份并保留回滚镜像；配置百度授权、登记核验过的构建，为每台可信设备**分别**签发 `tester` 权限的码并记录 `key_ref`，对每台设备验证经 SSH 转发的 `127.0.0.1:8765`。不得开放公网 `8765`；不得把 SSH 私钥/设备码写入公开仓库、Release、截图或网页。暂时复用管理员 SSH 私钥意味着电脑仍有**管理员级 SSH 权限**；Portal 密钥撤销只阻止后续目录/授权交接，不撤销 SSH 权限或已交付的广范围百度 owner token。最终需迁移到仅转发权限的独立 SSH 身份。

**从源码运行（Python 3.11+，Linux/macOS 旧 CLI）：**在本仓库根目录：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m dev_portal configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN
.venv/bin/python -m dev_portal serve
```

旧 CLI 的 `configure` 会隐藏密钥输入，但其本地 JSON 配置不同于 Windows 单码 DPAPI 状态，不能当作新版记住登录；`serve` 仅监听本机，默认下载目录为 `~/Downloads/DevPortal`。只有在**本设备**已经有授权隧道时，才能使用 `http://127.0.0.1:PORT`。本仓库不包含 DevHub 服务端、管理员凭据或 SSH 身份。客户端不自动更新，项目未指定开源许可证。

离线测试：`python -m unittest discover -s tests -v`。

## English

DevPortal runs on the **recipient's own Windows PC**. Its Python worker creates a private local SSH tunnel with a pinned public server key; the browser sees only the local project page, **never** the pairing code or Baidu handoff. The client downloads directly from Baidu with validated resume, exact size and SHA-256. DevHub does not relay file bytes.

**Important: stop v0.2.0-rc.1 downloads on Windows.** This preview can process binary archive segments through the Windows CRT text mode, corrupting bytes or failing with `Partial file changed before verification`. This is a **client defect, not an invalid pairing code**. Close the app; make a private copy of `%USERPROFILE%\Downloads\DevPortal\.devportal\` (including hidden `.part` and `.json` files) before any further attempt, and await a Windows-tested fix. The old ZIP will not update itself. The operator has verified the private Portal service and a personal tester code; a full download on the recipient's own PC remains unverified. Historical [v0.1.0-rc.1](https://github.com/bayzhen/DevPortal/releases/tag/v0.1.0-rc.1) does **not** support one-code sign-in.

### Recipient: three steps (after the fixed preview and private service are ready)

1. Once the operator confirms a Windows-tested fix, download the **fixed** Windows x64 ZIP and `SHA256SUMS.txt` from [DevPortal Releases](https://github.com/bayzhen/DevPortal/releases), **not** v0.2.0-rc.1. Verify SHA-256 and extract the entire `DevPortal` folder to a new location. The portable executable is unsigned.
2. Double-click `DevPortal.exe`; paste your **personal long pairing code only into its native dialog**, optionally check “在这台电脑记住登录” (Remember on this computer; off by default), then choose “登录并打开项目” (Sign in and open project). Never paste the code into a browser or manage a URL, port or SSH command yourself.
3. Choose an authorized build on the automatically opened local project page. Its **target platform** is displayed; a Linux build downloaded on Windows will not run on Windows. Keep the application open during downloads; sign in and retry after a disruption to use normal validated resume.

Your Windows account must **already have an operator-authorized SSH identity** and Windows system OpenSSH. For safety the client ignores arbitrary user SSH config and ProxyCommand: the identity must be available from the system SSH agent or at a default OpenSSH identity path such as `%USERPROFILE%\.ssh\id_ed25519`. A custom `IdentityFile` available only through a user's SSH alias is not automatically loaded; the operator must prepare this prerequisite before delivery, without asking the recipient to run SSH commands. The code contains neither an SSH private key nor an admin password or Baidu token. If the identity is missing, the host key changes, or the private service is unavailable, contact the operator; do **not** disable host verification or expose a public plaintext HTTP port. “Forget login” removes the saved code; declining to remember writes no reusable device code. Windows memory uses per-user DPAPI protection, not transferable to another PC. The pairing string is a reusable bearer invitation, **not** a short, expiring one-time redemption code; deliver it over a trusted private channel.

**Recovery boundary (only for a future Windows-accepted fixed preview):** the worker checks pinned SSH identity, obtains an authorized project catalog and fresh handoff, transfers Baidu byte ranges into a private checkpoint, verifies exact size and full SHA-256, and publishes without replacing a target. After reconnecting in the native dialog choose `Download / resume` on the project page; transient progress errors retry, or choose Refresh. Only an **empty** orphan `.part` before its first checkpoint can restart safely. Nonempty orphans, changed identities and damaged checkpoints stay untouched for private operator review. An already published file counts as complete only after independent size/SHA-256 verification, never from its name alone. “Forget login” does not delete download state. Back up old rc.1 partials before further attempts: damaged prefixes are not claimed recoverable. Source/hosted CI is not physical Windows full-download evidence, and **no new Windows Release exists yet**.

**Operator first:** Review and narrowly deploy the Portal backend after a stopped-writer backup, retain a rollback image, privately provision Baidu, register independently verified builds, issue one `tester` code per trusted device and retain its `key_ref`. Verify access through a recipient-owned SSH forward to remote `127.0.0.1:8765`; never publicly expose port `8765` or include codes, admin keys and secrets in the Release. Reusing an existing administrator SSH private key leaves that PC with **administrator-grade SSH access**. Revoking a Portal key only stops future catalog and handoff requests; it neither revokes SSH authority nor previously delivered broad Baidu owner tokens. Migrate to a dedicated forwarding-only SSH identity.

**Source CLI (Python 3.11+, Linux/macOS legacy flow):** From this repository's root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m dev_portal configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN
.venv/bin/python -m dev_portal serve
```

The CLI hides input but stores its legacy local JSON separately from the new Windows DPAPI pairing state. `serve` is loopback-only and defaults to `~/Downloads/DevPortal`. Use `http://127.0.0.1:PORT` only when an authorized tunnel is on **that recipient's own device**. This repository includes no DevHub backend or SSH identity. There is no automatic update or selected open-source license.

Offline tests: `python -m unittest discover -s tests -v`.
