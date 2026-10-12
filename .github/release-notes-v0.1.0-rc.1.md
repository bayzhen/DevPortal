## 中文

DevPortal **v0.1.0-rc.1 预览版**：仅含 Windows x64 便携客户端，**未签名**，不包含游戏包、DevHub 服务端或任何凭据。它由本标签源码在 GitHub Actions 的 Windows runner 上通过离线测试、启动与 UI 静态文件存在性检查及私密文件名筛查后构建。

1. 下载 `DevPortal-windows-x64.zip` 和 `SHA256SUMS.txt`，核对 ZIP 的 SHA-256。
2. 解压并保留整个 `DevPortal` 文件夹，在其上级目录运行：
   `.\DevPortal\DevPortal.exe configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN`，然后运行 `.\DevPortal\DevPortal.exe serve`。
3. 配置时按隐藏提示输入管理员**单独签发**的设备密钥，在本机打开打印的地址。

**当前限制（2026-10-13）：**Portal 服务端、设备密钥与独立设备入口尚未部署；此客户端发布**不等于**真实下载已可用。管理员必须先在私有项目完成授权、受控服务端部署及 HTTPS/使用者设备上的私有隧道。不要公开 DevHub 端口或共享百度 owner 凭据。Linux 游戏包不会因为下载到 Windows 就变成 Windows 可执行程序。本仓库尚未指定开源许可证。

## English

DevPortal **v0.1.0-rc.1 preview** contains only the **unsigned** Windows x64 portable client—not game packages, a DevHub backend, or credentials. GitHub Actions builds it from this tag on Windows after offline tests, launcher startup and static UI file checks, and a scan for private-data-like filenames.

1. Download `DevPortal-windows-x64.zip` and `SHA256SUMS.txt`; verify the ZIP's SHA-256.
2. Extract and keep the entire `DevPortal` folder. From its parent directory, run `.\DevPortal\DevPortal.exe configure --hub-url https://YOUR-PRIVATE-DEVHUB-ORIGIN`, then `.\DevPortal\DevPortal.exe serve`.
3. Enter your **individually issued** key at the hidden prompt; open the printed URL on that computer.

**Current limitation (2026-10-13):** The Portal backend, per-device keys, and independent-device ingress are **not deployed**. Publishing this client does **not** make real downloads available yet. The operator must first authorize and deploy the private backend and provide HTTPS or a private tunnel on the recipient's device. Do not expose the DevHub port or share Baidu owner credentials. A Linux game package remains Linux-only when downloaded on Windows. No open-source license has been selected.
