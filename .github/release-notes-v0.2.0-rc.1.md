## 中文

DevPortal **v0.2.0-rc.1 Windows x64 预览版**：双击 `DevPortal.exe`，在原生窗口粘贴管理员私下签发的**个人长配对码**，可选勾选“在这台电脑记住登录”，即可在**私有后端开通之后**看到获授权的项目构建并下载。新客户端用 Windows 系统 OpenSSH 自动建立**严格校验服务器 Ed25519 公钥**的本机隧道；浏览器只接收非密的本机构建元数据。记住的码由当前账户 Windows DPAPI 加密；不记住不会保存可复用的设备码。客户端会校验完成文件的大小和 SHA-256。

1. 下载 `DevPortal-windows-x64.zip` 和 `SHA256SUMS.txt`，检查 SHA-256，并解压**整个** `DevPortal` 文件夹。
2. 这台电脑需已配置管理员授权的 SSH 身份和 Windows 系统 OpenSSH；码不包含 SSH 私钥。**只在应用的原生窗口**粘贴私下收到的码，切勿粘贴到浏览器、截图或仓库；缺少 SSH 身份或服务器指纹变化时联系管理员。
3. 选择项目构建，注意 Linux 构建不会在 Windows 运行。下载时保持应用打开；断线后重新登录和重试可安全续传。

此预览版**未签名**，不是自动更新。个人码是可复用的 bearer 邀请，**不是一次性短验证码**；管理员须逐台单独签发并可撤销 Portal 凭据。绝不公开 DevHub `8765` 端口。暂用管理员 SSH 身份仍具有管理员级 SSH 权限；Portal 撤销不撤销 SSH 或已经交付的百度 owner token。

**验收状态：**发布客户端及离线 CI 不证明私有 Portal 后端已部署，也不证明妻子电脑已连接或完成全量 SHA-256 下载；这些步骤需要另外记录实际验证证据。本包不含游戏文件、服务端、私钥、配对码或百度凭据。本项目尚未指定开源许可证。

## English

DevPortal **v0.2.0-rc.1 Windows x64 preview**: double-click `DevPortal.exe`, paste your **personal long code into the native application dialog**, optionally choose “在这台电脑记住登录” (Remember on this computer), and, **once the private service has been enabled**, browse and download authorized project builds. The app automatically starts an SSH tunnel pinned to the server's Ed25519 public key, while the browser receives only nonsecret local build metadata. Remembered codes are protected by per-user Windows DPAPI; declining to remember leaves no reusable code on disk. Finished files are checked for exact size and SHA-256.

1. Download the ZIP and `SHA256SUMS.txt`; verify SHA-256 and extract the **whole** `DevPortal` directory.
2. The PC must already have an authorized SSH identity and Windows system OpenSSH; the code does **not** contain a private SSH key. Paste the private code **only in the app dialog**, never a browser, screenshot or repository. Contact the operator if the SSH identity is missing or the host key changes.
3. Choose a project build; a Linux target will not run on Windows. Keep the app open while downloading; reconnect and retry for validated resume after a disruption.

This preview is **unsigned** and has no automatic updates. The code is a reusable bearer invitation, **not** an expiring one-time short code. Operators issue and revoke separate Portal credentials per device and never publicly expose DevHub port `8765`. Reusing an administrator SSH identity still grants admin-level SSH authority; revoking a Portal key cannot revoke SSH or already handed-off broad Baidu owner tokens.

**Acceptance status:** A published client and offline CI do **not** prove the private Portal backend is deployed or the recipient's actual Windows PC has finished a fully hashed download. Those are separately gated checks. This ZIP contains no game build, backend, SSH private key, pairing code or Baidu credential. No open-source license has been selected.
