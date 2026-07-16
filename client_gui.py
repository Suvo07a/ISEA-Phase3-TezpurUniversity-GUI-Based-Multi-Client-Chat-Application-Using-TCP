import socket
import threading
import queue
import time
import hashlib
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

SERVER_PORT = 5000

# ---------------------------------------------------------
# Color theme
# ---------------------------------------------------------
BG_DARK = "#1e2530"
BG_SIDEBAR = "#252d3a"
BG_CHAT = "#f4f6f9"
ACCENT = "#4f8cff"
ACCENT_DARK = "#3a6fd1"
TEXT_LIGHT = "#e8ecf1"
TEXT_MUTED = "#9aa5b5"
ONLINE_GREEN = "#3ecf6e"
OFFLINE_RED = "#e05252"
SERVER_MSG = "#8a8f98"

USER_COLORS = [
    "#e05252", "#3ecf6e", "#4f8cff", "#e0a352",
    "#a352e0", "#52c7e0", "#e052b0", "#7ac943",
]


def color_for_user(name):
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    return USER_COLORS[h % len(USER_COLORS)]


# ---------------------------------------------------------
# Networking layer (kept separate from GUI code)
# ---------------------------------------------------------
class ChatClientNetwork:
    def __init__(self, msg_queue):
        self.sock = None
        self.username = None
        self.connected = False
        self.msg_queue = msg_queue
        self._buffer = ""

    def _read_line(self):
        while "\n" not in self._buffer:
            chunk = self.sock.recv(1024).decode(errors="ignore")
            if not chunk:
                raise ConnectionError("Server closed connection")
            self._buffer += chunk
        line, self._buffer = self._buffer.split("\n", 1)
        return line.strip()

    def connect(self, server_ip, username, password):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect((server_ip, SERVER_PORT))

        self._read_line()  # "USERNAME:"
        self.sock.sendall(username.encode())

        prompt = self._read_line()  # "PASSWORD:" / "NEW_PASSWORD:" / "ERROR:..."
        if prompt.startswith("ERROR:"):
            raise ConnectionError(prompt[len("ERROR:"):])

        self.sock.sendall(password.encode())

        result = self._read_line()
        if result.startswith("REGISTERED:"):
            result = self._read_line()
        if result.startswith("ERROR:"):
            raise ConnectionError(result[len("ERROR:"):])
        if not result.startswith("AUTH_OK"):
            raise ConnectionError("Unexpected response during authentication")

        self.sock.settimeout(None)
        self.username = username
        self.connected = True

        t = threading.Thread(target=self._receive_loop, daemon=True)
        t.start()

        self.send_raw("/list")

    def _receive_loop(self):
        while self.connected:
            try:
                if self._buffer:
                    data, self._buffer = self._buffer, ""
                else:
                    raw = self.sock.recv(2048)
                    if not raw:
                        break
                    data = raw.decode(errors="ignore")
                self.msg_queue.put(("data", data))
            except OSError:
                break
        self.connected = False
        self.msg_queue.put(("disconnected", ""))

    def send_raw(self, text):
        if self.connected:
            try:
                self.sock.sendall(text.encode())
            except OSError:
                pass

    def send_broadcast(self, text):
        self.send_raw(text)

    def send_private(self, target, text):
        self.send_raw(f"/msg {target} {text}")

    def send_logout(self):
        self.send_raw("/logout")

    def request_user_list(self):
        self.send_raw("/list")

    def disconnect(self):
        self.connected = False
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------
# Login window
# ---------------------------------------------------------
class LoginWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Chat Login")
        self.root.geometry("380x420")
        self.root.configure(bg=BG_DARK)
        self.root.resizable(False, False)
        self.result = None

        card = tk.Frame(root, bg=BG_SIDEBAR)
        card.place(relx=0.5, rely=0.5, anchor="center", width=320, height=380)

        tk.Label(card, text="Chat", font=("Segoe UI", 22, "bold"),
                 bg=BG_SIDEBAR, fg=ACCENT).pack(pady=(30, 5))
        tk.Label(card, text="Sign in to join the room", font=("Segoe UI", 10),
                 bg=BG_SIDEBAR, fg=TEXT_MUTED).pack(pady=(0, 20))

        self.ip_entry = self._labeled_entry(card, "SERVER IP")
        self.ip_entry.insert(0, "10.0.0.1")

        self.user_entry = self._labeled_entry(card, "USERNAME")

        self.pass_entry = self._labeled_entry(card, "PASSWORD", show="*")

        self.status_label = tk.Label(card, text="", bg=BG_SIDEBAR,
                                      fg=OFFLINE_RED, font=("Segoe UI", 9))
        self.status_label.pack(pady=(5, 0))

        connect_btn = tk.Button(
            card, text="Connect", command=self.on_connect,
            bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
            activeforeground="white", relief="flat",
            font=("Segoe UI", 11, "bold"), cursor="hand2",
        )
        connect_btn.pack(fill="x", padx=25, pady=(15, 10), ipady=8)

        self.user_entry.focus()
        self.root.bind("<Return>", lambda e: self.on_connect())

    def _labeled_entry(self, parent, label, show=None):
        tk.Label(parent, text=label, bg=BG_SIDEBAR, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=25, pady=(8, 0))
        entry = tk.Entry(parent, bg="#1a202b", fg=TEXT_LIGHT,
                          insertbackground=TEXT_LIGHT, relief="flat",
                          font=("Segoe UI", 11), show=show)
        entry.pack(fill="x", padx=25, ipady=6)
        return entry

    def on_connect(self):
        ip = self.ip_entry.get().strip()
        username = self.user_entry.get().strip()
        password = self.pass_entry.get()

        if not username:
            self.status_label.config(text="Username cannot be empty!")
            return
        if not password:
            self.status_label.config(text="Password cannot be empty!")
            return
        if not ip:
            self.status_label.config(text="Server IP cannot be empty!")
            return

        self.status_label.config(text="Connecting...", fg=ACCENT)
        self.root.update()

        self.result = (ip, username, password)
        self.root.quit()


# ---------------------------------------------------------
# Chat window
# ---------------------------------------------------------
class ChatWindow:
    def __init__(self, root, network, username):
        self.root = root
        self.network = network
        self.username = username
        self.msg_queue = network.msg_queue

        self.root.title(f"Team Chat - {username}")
        self.root.geometry("820x560")
        self.root.configure(bg=BG_CHAT)
        self.root.protocol("WM_DELETE_WINDOW", self.on_disconnect)

        self._build_menu()
        self._build_layout()
        self._append_system(f"Connected as {username}")
        self.poll_queue()

    # ---------------- menu ----------------
    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Disconnect", command=self.on_disconnect)
        file_menu.add_command(label="Logout", command=self.on_logout)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Commands", command=self._show_commands)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _show_commands(self):
        messagebox.showinfo(
            "Commands",
            "Type a message and press Send to broadcast.\n\n"
            "Check 'Private', select a user, then Send for a private message.\n\n"
            "/list - refresh online users\n"
            "/stats - show server statistics\n"
            "/logout - log out",
        )

    def _show_about(self):
        messagebox.showinfo(
            "About",
            f"GUI Chat Client\nAssignment 7 - Secure TCP Chat Application\nUser: {self.username}",
        )

    # ---------------- layout ----------------
    def _build_layout(self):
        topbar = tk.Frame(self.root, bg=BG_DARK, height=44)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        self.status_dot = tk.Canvas(topbar, width=12, height=12, bg=BG_DARK,
                                     highlightthickness=0)
        self.status_dot.pack(side="left", padx=(15, 5), pady=15)
        self.dot_id = self.status_dot.create_oval(1, 1, 11, 11, fill=ONLINE_GREEN, outline="")

        self.status_var = tk.StringVar(value="Connected")
        self.status_text = tk.Label(topbar, textvariable=self.status_var, bg=BG_DARK,
                                     fg=TEXT_LIGHT, font=("Segoe UI", 10, "bold"))
        self.status_text.pack(side="left")

        tk.Label(topbar, text=f"logged in as {self.username}", bg=BG_DARK,
                 fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(side="right", padx=15)

        body = tk.Frame(self.root, bg=BG_CHAT)
        body.pack(fill="both", expand=True)

        # Sidebar (online users)
        sidebar = tk.Frame(body, bg=BG_SIDEBAR, width=200)
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)

        self.user_count_label = tk.Label(sidebar, text="ONLINE - 0", bg=BG_SIDEBAR,
                                          fg=TEXT_MUTED, font=("Segoe UI", 9, "bold"))
        self.user_count_label.pack(anchor="w", padx=15, pady=(15, 5))

        list_frame = tk.Frame(sidebar, bg=BG_SIDEBAR)
        list_frame.pack(fill="both", expand=True, padx=10)

        self.user_listbox = tk.Listbox(
            list_frame, bg=BG_SIDEBAR, fg=TEXT_LIGHT, relief="flat",
            font=("Segoe UI", 10), selectbackground=ACCENT,
            selectforeground="white", activestyle="none",
            highlightthickness=0, bd=0,
        )
        self.user_listbox.pack(fill="both", expand=True)

        refresh_btn = tk.Button(
            sidebar, text="Refresh List", command=self.network.request_user_list,
            bg="#2f3846", fg=TEXT_LIGHT, activebackground="#3a4557",
            activeforeground="white", relief="flat", font=("Segoe UI", 9),
            cursor="hand2",
        )
        refresh_btn.pack(fill="x", padx=15, pady=(5, 5), ipady=5)

        disconnect_btn = tk.Button(
            sidebar, text="Disconnect", command=self.on_disconnect,
            bg=OFFLINE_RED, fg="white", activebackground="#c23f3f",
            activeforeground="white", relief="flat", font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        disconnect_btn.pack(fill="x", padx=15, pady=(0, 15), ipady=6)

        # Chat area
        chat_frame = tk.Frame(body, bg=BG_CHAT)
        chat_frame.pack(side="left", fill="both", expand=True)

        self.chat_area = scrolledtext.ScrolledText(
            chat_frame, state="disabled", wrap="word", bg=BG_CHAT,
            relief="flat", font=("Segoe UI", 10), padx=15, pady=10,
            borderwidth=0,
        )
        self.chat_area.pack(fill="both", expand=True, padx=10, pady=10)
        self._configure_tags()

        # Input row
        input_bar = tk.Frame(chat_frame, bg=BG_CHAT)
        input_bar.pack(fill="x", padx=10, pady=(0, 10))

        self.private_var = tk.BooleanVar()
        priv_check = tk.Checkbutton(
            input_bar, text="Private", variable=self.private_var,
            bg=BG_CHAT, fg="#333", font=("Segoe UI", 9),
            activebackground=BG_CHAT, selectcolor=BG_CHAT,
        )
        priv_check.pack(side="left", padx=(0, 8))

        entry_wrap = tk.Frame(input_bar, bg="white", highlightbackground="#d0d5dd",
                               highlightthickness=1)
        entry_wrap.pack(side="left", fill="x", expand=True)

        self.msg_entry = tk.Entry(entry_wrap, relief="flat", font=("Segoe UI", 11),
                                   bg="white")
        self.msg_entry.pack(fill="both", expand=True, padx=10, ipady=8)
        self.msg_entry.bind("<Return>", lambda e: self.on_send())
        self.msg_entry.focus()

        send_btn = tk.Button(
            input_bar, text="Send", command=self.on_send,
            bg=ACCENT, fg="white", activebackground=ACCENT_DARK,
            activeforeground="white", relief="flat", font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        send_btn.pack(side="left", padx=(8, 0), ipady=8, ipadx=14)

    def _configure_tags(self):
        self.chat_area.tag_configure("timestamp", foreground=TEXT_MUTED,
                                      font=("Segoe UI", 8))
        self.chat_area.tag_configure("system", foreground=SERVER_MSG,
                                      font=("Segoe UI", 9, "italic"))
        self.chat_area.tag_configure("private", foreground="#a352e0",
                                      font=("Segoe UI", 9, "italic"))
        self.chat_area.tag_configure("error", foreground=OFFLINE_RED,
                                      font=("Segoe UI", 9, "bold"))
        self.chat_area.tag_configure("body", foreground="#1a1a1a")

    # ---------------- actions ----------------
    def on_send(self):
        text = self.msg_entry.get().strip()
        if not text:
            return

        if text.startswith("/"):
            # Raw command typed manually (/list, /stats, /msg user text ...)
            self.network.send_raw(text)
            self._append_system(f"Sent command: {text}")
            self.msg_entry.delete(0, "end")
            return

        if self.private_var.get():
            selection = self.user_listbox.curselection()
            if not selection:
                messagebox.showwarning("No user selected",
                                        "Select a user in the list for a private message.")
                return
            target = self.user_listbox.get(selection[0])
            self.network.send_private(target, text)
            self._append_private(f"You -> {target}", text)
        else:
            self.network.send_broadcast(text)
            self._append_message(self.username, text, is_self=True)

        self.msg_entry.delete(0, "end")

    def on_disconnect(self):
        self.network.disconnect()
        self.status_var.set("Disconnected")
        self.status_dot.itemconfig(self.dot_id, fill=OFFLINE_RED)
        self.root.destroy()

    def on_logout(self):
        self.network.send_logout()
        self.root.after(300, self.on_disconnect)

    # ---------------- background message handling ----------------
    def poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "data":
                    self.handle_incoming(payload)
                elif kind == "disconnected":
                    self.status_var.set("Server closed connection")
                    self.status_dot.itemconfig(self.dot_id, fill=OFFLINE_RED)
        except queue.Empty:
            pass

        self.root.after(100, self.poll_queue)

    def handle_incoming(self, text):
        if "=== Online Users ===" in text:
            count = self._update_user_list(text)
            self._append_system(f"Online users updated ({count} online)")
            return
        if "=== Server Statistics ===" in text:
            self._append_system(text.strip())
            return
        if "[ERROR]" in text:
            self._append_error(text.strip())
            return
        if "[PRIVATE from" in text or "[PRIVATE to" in text:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("[PRIVATE from"):
                    sender = line.split("from", 1)[1].split("]")[0].strip()
                    body = line.split("]", 1)[1].strip()
                    self._append_private(f"{sender} -> You", body)
                elif line.startswith("[PRIVATE to"):
                    target = line.split("to", 1)[1].split("]")[0].strip()
                    body = line.split("]", 1)[1].strip()
                    self._append_private(f"You -> {target}", body)
            return
        if "[SERVER]" in text:
            for line in text.splitlines():
                if line.strip():
                    self._append_system(line.replace("[SERVER]", "").strip())
            if "joined the chat" in text or "left the chat" in text:
                self.network.request_user_list()
            return

        # Regular broadcast: "[username] message"
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") and "]" in line:
                sender = line[1:line.index("]")]
                body = line[line.index("]") + 1:].strip()
                self._append_message(sender, body, is_self=(sender == self.username))
            else:
                self._append_system(line)

    def _update_user_list(self, text):
        lines = text.splitlines()
        users = []
        for line in lines:
            line = line.strip()
            if line.startswith("→") or line.startswith("->"):
                users.append(line.replace("→", "").replace("->", "").strip())

        self.user_listbox.delete(0, "end")
        for u in users:
            self.user_listbox.insert("end", u)
            idx = self.user_listbox.size() - 1
            self.user_listbox.itemconfig(idx, fg=color_for_user(u))

        self.user_count_label.config(text=f"ONLINE - {len(users)}")
        return len(users)

    # ---------------- rendering helpers ----------------
    def _timestamp(self):
        return time.strftime("%H:%M")

    def _append_message(self, sender, body, is_self=False):
        self.chat_area.config(state="normal")
        tag_name = f"user_{sender}"
        if tag_name not in self.chat_area.tag_names():
            self.chat_area.tag_configure(tag_name, foreground=color_for_user(sender),
                                          font=("Segoe UI", 10, "bold"))

        label = "You" if is_self else sender
        self.chat_area.insert("end", f"{label}  ", tag_name)
        self.chat_area.insert("end", f"{self._timestamp()}\n", "timestamp")
        self.chat_area.insert("end", f"{body}\n\n", "body")
        self.chat_area.see("end")
        self.chat_area.config(state="disabled")

    def _append_private(self, label, body):
        self.chat_area.config(state="normal")
        self.chat_area.insert("end", f"[private] {label}  ", "private")
        self.chat_area.insert("end", f"{self._timestamp()}\n", "timestamp")
        self.chat_area.insert("end", f"{body}\n\n", "body")
        self.chat_area.see("end")
        self.chat_area.config(state="disabled")

    def _append_system(self, text):
        self.chat_area.config(state="normal")
        self.chat_area.insert("end", f"• {text}\n\n", "system")
        self.chat_area.see("end")
        self.chat_area.config(state="disabled")

    def _append_error(self, text):
        self.chat_area.config(state="normal")
        self.chat_area.insert("end", f"{text}\n\n", "error")
        self.chat_area.see("end")
        self.chat_area.config(state="disabled")


# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------
def main():
    login_root = tk.Tk()
    login = LoginWindow(login_root)
    login_root.mainloop()

    if login.result is None:
        return

    server_ip, username, password = login.result
    login_root.destroy()

    msg_queue = queue.Queue()
    network = ChatClientNetwork(msg_queue)

    try:
        network.connect(server_ip, username, password)
    except Exception as e:
        error_root = tk.Tk()
        error_root.withdraw()
        messagebox.showerror("Connection Failed", str(e))
        error_root.destroy()
        return

    chat_root = tk.Tk()
    ChatWindow(chat_root, network, username)
    chat_root.mainloop()


if __name__ == "__main__":
    main()
