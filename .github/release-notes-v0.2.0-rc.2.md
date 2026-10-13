## 中文

DevPortal **v0.2.0-rc.2 Windows x64 修复预览版**，修复已发布 v0.2.0-rc.1 在 Windows 上读写二进制下载文件时误用文本模式的问题。旧版可能改写归档字节，或重启续传时显示 `Partial file changed before verification`；**这不是配对码过期**。新版使用二进制文件模式，先校验已提交断点再丢弃未提交的尾部字节；若内容不匹配则保留文件、隔离断点，不会强行追加。Windows CI 使用含 CRLF、Ctrl-Z 和非文本字节的合成归档验证了中断、重新授权续传、完整内容和 SHA-256。CI **不代表**任何人的真实设备已经下载完整 1.52 GB 文件。

1. 如果用过旧版，请先退出程序，在自己的 Windows 账户中**私密复制**整个 `%USERPROFILE%\Downloads\DevPortal\.devportal\`（包括隐藏的 `.part`、`.json`）保存原有进度，再继续。不要删除、编辑或公开这些文件和个人码。v0.2.0-rc.1 不会自动更新。
2. 下载本版本的 `DevPortal-windows-x64.zip` 和 `SHA256SUMS.txt`，核对本版本 SHA-256，把整个 `DevPortal` 文件夹解压到**新目录**。双击 `DevPortal.exe`，仅在原生窗口粘贴管理员私下发给你的个人码；可选择在这台电脑记住登录。电脑必须已有授权 SSH 身份和系统 OpenSSH，客户端会校验服务器公钥并自行建立本机隧道。
3. 在本机项目页下载授权构建。若旧断点出现 `quarantined`、身份不一致或长度不符，**不要**反复重试、手动改写 checkpoint 或运行可疑文件；保留先前备份，请管理员核对后协助重新开始。仅当完成文件的大小及 SHA-256 与目录值一致才视为成功。Linux 构建无法在 Windows 上运行。

本预览版未签名、无自动更新。个人码是长期 bearer 邀请，不是一次性短码；旧有管理员 SSH 身份仍可具有管理员权限，Portal 密钥撤销不能撤销已授出的 SSH 权限或百度 owner token。ZIP 不含游戏构建、服务端、私钥、配对码或百度凭据。本项目尚未选定开源许可证。

## English

DevPortal **v0.2.0-rc.2 Windows x64 repair preview** fixes a v0.2.0-rc.1 bug that opened binary partial downloads in the Windows CRT's text mode. The old release could alter archive bytes or show `Partial file changed before verification` after restart; **this is not an expired pairing code**. The repaired client uses binary mode, verifies the committed prefix *before* truncating an uncommitted crash tail, and preserves a mismatched partial while quarantining its checkpoint instead of appending. Native Windows CI covers an offline synthetic archive containing CRLF, Ctrl-Z and non-text bytes, interruption, fresh-handoff resume and exact SHA-256. CI does **not** prove a recipient's complete real-world 1.52 GB transfer.

1. If you used the old release, close it and privately **copy the entire** `%USERPROFILE%\Downloads\DevPortal\.devportal\` folder (including hidden `.part` and `.json` files) before further attempts. Do not delete, edit or share those files or your personal code. The old ZIP does not auto-update.
2. Download **this version's** ZIP and `SHA256SUMS.txt`, verify its SHA-256, and extract the whole `DevPortal` folder into a **new directory**. Open `DevPortal.exe` and paste your private code only in its native dialog; remembering is optional. This PC needs its existing authorized SSH identity and system OpenSSH. The client pins the server key and starts the private local tunnel.
3. Download only your authorized build from the local project page. If the old checkpoint is quarantined or its identity/size differs, **do not** force a retry, edit the checkpoint or run an unverified file; keep your backup and ask the operator to help start cleanly. Only a completed file with matching size and SHA-256 is accepted. A Linux-target build cannot run on Windows.

This unsigned preview has no automatic updates. The code is a reusable bearer invitation, not a one-time short code. An existing admin-grade SSH identity remains admin-grade; Portal-key revocation does not revoke SSH or a previously handed-off Baidu owner token. The ZIP contains no game build, backend, SSH private key, pairing code or Baidu credentials. No open-source license has been selected.
