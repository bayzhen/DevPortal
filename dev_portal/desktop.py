"""Native desktop sign-in; the browser receives only authorized build metadata.

No pairing code is posted to the loopback HTTP server. The GUI is imported only
for bare desktop launch, keeping source CLI operation and headless tests intact.
"""
from __future__ import annotations

from pathlib import Path
import logging
import queue
import threading
import webbrowser

from .client import HubClient, PortalError
from .pairing import Pairing, PairingError, decode_pairing
from .remember import RememberError, forget_remembered, load_remembered, save_remembered
from .server import PortalServer
from .tunnel import Tunnel, TunnelError


class DesktopError(Exception):
    """Only fixed, credential-free messages are displayed to the recipient."""


class DesktopSession:
    """One owned SSH transport and one local page, closed together on failure."""

    def __init__(self, *, tunnel_factory=Tunnel, hub_factory=HubClient,
                 server_factory=PortalServer, downloads: Path | None = None,
                 remember_loader=load_remembered, remember_saver=save_remembered,
                 remember_forgetter=forget_remembered):
        self.tunnel_factory = tunnel_factory
        self.hub_factory = hub_factory
        self.server_factory = server_factory
        self.downloads = downloads or Path.home() / "Downloads" / "DevPortal"
        self.remember_loader = remember_loader
        self.remember_saver = remember_saver
        self.remember_forgetter = remember_forgetter
        self.tunnel = None
        self.server = None
        self.thread = None
        self.pairing = None
        self.project = None
        self._remember_lock = threading.RLock()
        self._forget_revision = 0

    def remembered(self) -> Pairing | None:
        return self.remember_loader()

    def forget(self) -> None:
        with self._remember_lock:
            self.remember_forgetter()
            self._forget_revision += 1

    def connect(self, pairing: Pairing, *, remember: bool) -> str:
        if self.tunnel is not None or self.server is not None:
            raise DesktopError("A project session is already open")
        with self._remember_lock:
            revision = self._forget_revision
        tunnel = None
        server = None
        worker = None
        started = False
        serving = False
        try:
            tunnel = self.tunnel_factory(pairing).start()
            hub = self.hub_factory(tunnel.origin, pairing.portal_key, tunnel.transport())
            builds = hub.builds()  # Authenticated catalog before browser or saved login.
            if any(build.project_ref != pairing.project_ref for build in builds):
                raise DesktopError("Project identity did not match; contact the operator")
            server = self.server_factory(hub, self.downloads, expected_project=pairing.project_ref)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True)
            worker.start()
            started = True
            # Thread.start() does not prove serve_forever has entered its loop.
            # Wait before a remember failure can race shutdown with startup.
            serving = server.wait_ready()
            if not serving:
                raise DesktopError("Local project page unavailable; retry")
            if remember:
                with self._remember_lock:
                    if revision == self._forget_revision:
                        self.remember_saver(pairing)
            else:
                self.forget()
            # No session is visible until the listener and remember choice succeed.
            self.tunnel, self.server, self.thread = tunnel, server, worker
            self.pairing = pairing
            self.project = pairing.project_ref if builds else None
            return server.origin
        except Exception as error:
            if server is not None:
                logging.getLogger(__name__).warning(
                    "session startup project_ref=%s phase=%s result=failed "
                    "reason=%s bearer=unavailable",
                    pairing.project_ref, "listener" if not serving else "memory",
                    "thread_not_started" if not started else "startup_or_storage_failure",
                )
            try:
                if server is not None:
                    try:
                        if serving:
                            server.shutdown()
                    finally:
                        server.server_close()
                        if started and worker is not None:
                            worker.join(timeout=2)
            finally:
                if tunnel is not None:
                    tunnel.close()
            if isinstance(error, (PairingError, PortalError, RememberError, TunnelError, OSError, DesktopError)):
                raise
            raise DesktopError("Project connection unavailable; contact the operator") from None

    def close(self) -> None:
        server, tunnel, worker = self.server, self.tunnel, self.thread
        self.server = self.tunnel = self.thread = None
        self.pairing = self.project = None
        try:
            if server is not None:
                try:
                    if worker is not None and worker.is_alive():
                        server.shutdown()
                finally:
                    server.server_close()
                    if worker is not None:
                        worker.join(timeout=2)
        finally:
            if tunnel is not None:
                tunnel.close()


def run_desktop() -> int:
    """Show native paste/remember controls; keep the tunnel alive while UI runs."""
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
    except Exception:
        # A frozen GUI build cannot rely on stderr; the caller also returns 2.
        return 2
    session = DesktopSession()
    root.title("GDUE DevPortal — 项目下载")
    root.geometry("510x240")
    root.resizable(False, False)
    outer = ttk.Frame(root, padding=18)
    outer.pack(fill="both", expand=True)
    title = ttk.Label(outer, text="GameDevUE · 项目下载", font=("Segoe UI", 15, "bold"))
    title.pack(anchor="w", pady=(0, 12))
    prompt = ttk.Label(outer, text="粘贴管理员发给你的专属配对码：")
    prompt.pack(anchor="w")
    code_var = tk.StringVar()
    entry = ttk.Entry(outer, textvariable=code_var, show="•", width=62)
    entry.pack(fill="x", pady=(5, 8))
    entry.focus_set()
    remember_var = tk.BooleanVar(value=False)
    check = ttk.Checkbutton(outer, text="在这台电脑记住登录", variable=remember_var)
    check.pack(anchor="w")
    status_var = tk.StringVar(value="首次连接需要这台电脑已有管理员授权的 SSH 身份。")
    status = ttk.Label(outer, textvariable=status_var, wraplength=460)
    status.pack(anchor="w", pady=(12, 8))
    buttons = ttk.Frame(outer)
    buttons.pack(anchor="w")
    sign_in = ttk.Button(buttons, text="登录并打开项目")
    sign_in.pack(side="left")
    open_page = ttk.Button(buttons, text="打开项目", state="disabled")
    open_page.pack(side="left", padx=8)
    forget = ttk.Button(buttons, text="忘记登录")
    forget.pack(side="left", padx=8)
    ttk.Button(buttons, text="退出", command=root.destroy).pack(side="left", padx=8)
    events: queue.Queue[tuple[str, object]] = queue.Queue()
    busy = False
    browser_url = None
    login_thread = None
    shutting_down = False

    def begin(pairing: Pairing, remember: bool) -> None:
        nonlocal busy, login_thread
        if busy or session.server is not None:
            return
        busy = True
        sign_in.configure(state="disabled")
        entry.configure(state="disabled")
        check.configure(state="disabled")
        forget.configure(state="disabled")
        status_var.set("正在安全连接项目…")
        def worker() -> None:
            try:
                url = session.connect(pairing, remember=remember)
                events.put(("success", url))
            except (PairingError, PortalError, RememberError, TunnelError, DesktopError):
                events.put(("failure", "无法连接项目；请检查网络或联系管理员。"))
            except Exception:
                events.put(("failure", "无法连接项目；请联系管理员。"))
        login_thread = threading.Thread(target=worker, daemon=True)
        login_thread.start()

    def submit(*_args: object) -> None:
        try:
            pairing = decode_pairing(code_var.get().strip())
        except PairingError:
            status_var.set("配对码无效；请联系管理员重新发送。")
            return
        code_var.set("")
        begin(pairing, bool(remember_var.get()))

    def clear_login() -> None:
        if busy:
            status_var.set("登录尚未结束；请稍后再忘记登录。")
            return
        try:
            session.forget()
        except RememberError:
            status_var.set("无法清除登录；请联系管理员。")
        else:
            status_var.set("这台电脑已忘记登录；下次需要重新粘贴配对码。")

    def process_events() -> None:
        nonlocal busy, browser_url
        try:
            while True:
                event, value = events.get_nowait()
                if shutting_down:
                    continue
                busy = False
                forget.configure(state="normal")
                if event == "success":
                    browser_url = str(value)
                    sign_in.configure(state="disabled")
                    open_page.configure(state="normal")
                    entry.configure(state="disabled")
                    check.configure(state="disabled")
                    status_var.set("项目已连接。浏览器未自动打开时，请点“打开项目”。")
                    webbrowser.open(browser_url)
                    if session.tunnel is not None:
                        def watcher(tunnel) -> None:
                            tunnel.wait()
                            events.put(("lost", None))
                        threading.Thread(target=watcher, args=(session.tunnel,), daemon=True).start()
                elif event == "lost":
                    session.close()
                    browser_url = None
                    open_page.configure(state="disabled")
                    entry.configure(state="normal")
                    check.configure(state="normal")
                    sign_in.configure(state="normal")
                    status_var.set("连接中断；请检查网络后重新粘贴配对码。已下载的进度不会清除。")
                else:
                    sign_in.configure(state="normal")
                    entry.configure(state="normal")
                    check.configure(state="normal")
                    status_var.set(str(value))
        except queue.Empty:
            pass
        if root.winfo_exists():
            root.after(150, process_events)

    def open_project() -> None:
        if browser_url and session.tunnel and session.tunnel.alive:
            webbrowser.open(browser_url)

    def on_close() -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        root.withdraw()
        def finish() -> None:
            if login_thread is not None and login_thread.is_alive():
                root.after(150, finish)
                return
            session.close()
            root.destroy()
        finish()

    sign_in.configure(command=submit)
    entry.bind("<Return>", submit)
    open_page.configure(command=open_project)
    forget.configure(command=clear_login)
    root.protocol("WM_DELETE_WINDOW", on_close)
    # The exit button follows the same cleanup path as the window close button.
    for child in buttons.winfo_children():
        if isinstance(child, ttk.Button) and child.cget("text") == "退出":
            child.configure(command=on_close)
    try:
        remembered = session.remembered()
    except RememberError:
        status_var.set("记住的登录不可用；可重新粘贴配对码，或点“忘记登录”。")
        remembered = None
    if remembered is not None:
        remember_var.set(True)
        begin(remembered, True)
    root.after(150, process_events)
    try:
        root.mainloop()
    finally:
        session.close()
    return 0
