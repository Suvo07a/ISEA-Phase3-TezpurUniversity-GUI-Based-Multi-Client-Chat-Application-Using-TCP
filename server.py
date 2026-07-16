import socket
import threading
import datetime
import csv
import os
import json
import hashlib
import re
import time

HOST = '0.0.0.0'
PORT = 5000

USERS_FILE = "users.json"
LOG_FILE = "security_log.txt"

MAX_MESSAGE_LEN = 500
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
INACTIVITY_TIMEOUT = 300  # 5 minutes

USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,20}$')
ALLOWED_COMMANDS = {"/msg", "/list", "/stats", "/logout"}

# {username: {conn, ip, port, login_time, status}}
clients = {}
clients_lock = threading.Lock()

stats = {
    "total_messages": 0,
    "broadcast_messages": 0,
    "private_messages": 0
}

chat_history = []

# ---------------- user database ----------------
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return {}

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(users_db, f, indent=2)

users_db = load_users()
users_lock = threading.Lock()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ---------------- failed login tracking ----------------
failed_attempts = {}  # username -> {"count": int, "locked_until": float}
failed_lock = threading.Lock()

def is_locked(username):
    with failed_lock:
        info = failed_attempts.get(username)
        if info and info["locked_until"] > time.time():
            return True, int(info["locked_until"] - time.time())
        return False, 0

def register_failure(username):
    with failed_lock:
        info = failed_attempts.setdefault(username, {"count": 0, "locked_until": 0})
        info["count"] += 1
        if info["count"] >= MAX_FAILED_ATTEMPTS:
            info["locked_until"] = time.time() + LOCKOUT_SECONDS
            info["count"] = 0

def clear_failures(username):
    with failed_lock:
        failed_attempts.pop(username, None)

# ---------------- secure logging (never logs passwords) ----------------
def log_security(event, username, ip, detail=""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event} | user={username} | ip={ip} | {detail}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)
    print(f"[SECURITY] {line.strip()}")

def log_event(event, username, ip):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[SERVER] {ts},{event},{username},{ip}")

# ---------------- chat history ----------------
def save_history(sender, receiver, msg_type, message):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    chat_history.append([ts, sender, receiver, msg_type, message])
    with open("chat_history.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "sender", "receiver", "message_type", "message"])
        w.writerows(chat_history)

def get_last_five(username):
    user_msgs = [r for r in chat_history if r[1] == username]
    return user_msgs[-5:]

# ---------------- messaging ----------------
def broadcast(message, sender=None):
    with clients_lock:
        for uname, info in clients.items():
            if uname != sender:
                try:
                    info["conn"].sendall(message.encode())
                except:
                    pass
    stats["broadcast_messages"] += 1

def send_private(sender, target, message):
    with clients_lock:
        if target in clients:
            try:
                msg = f"[PRIVATE from {sender}] {message}\n"
                clients[target]["conn"].sendall(msg.encode())
                clients[sender]["conn"].sendall(
                    f"[PRIVATE to {target}] {message}\n".encode())
                stats["private_messages"] += 1
                save_history(sender, target, "private", message)
                return True
            except:
                return False
        return False

def send_user_list(conn):
    with clients_lock:
        user_list = list(clients.keys())
    msg = "=== Online Users ===\n"
    for u in user_list:
        msg += f"  → {u}\n"
    msg += "==================\n"
    conn.sendall(msg.encode())

# ---------------- authentication ----------------
def authenticate(conn, addr):
    client_ip = addr[0]
    conn.sendall("USERNAME:\n".encode())
    raw = conn.recv(1024)
    if not raw:
        return None
    username = raw.decode(errors="ignore").strip()

    if not USERNAME_RE.match(username):
        conn.sendall("ERROR:Invalid username (3-20 letters/numbers/underscore only)\n".encode())
        log_security("INVALID_USERNAME", username or "?", client_ip)
        return None

    with clients_lock:
        if username in clients:
            conn.sendall("ERROR:User already logged in elsewhere\n".encode())
            log_security("DUPLICATE_LOGIN_BLOCKED", username, client_ip)
            return None

    locked, remaining = is_locked(username)
    if locked:
        conn.sendall(f"ERROR:Account locked. Try again in {remaining}s\n".encode())
        log_security("LOGIN_BLOCKED_LOCKOUT", username, client_ip)
        return None

    with users_lock:
        exists = username in users_db

    conn.sendall(("PASSWORD:\n" if exists else "NEW_PASSWORD:\n").encode())

    raw_pw = conn.recv(1024)
    if not raw_pw:
        return None
    password = raw_pw.decode(errors="ignore").strip()

    if not password:
        conn.sendall("ERROR:Password cannot be empty\n".encode())
        log_security("EMPTY_PASSWORD", username, client_ip)
        return None

    pw_hash = hash_password(password)

    if exists:
        with users_lock:
            stored_hash = users_db[username]["password_hash"]
        if pw_hash != stored_hash:
            register_failure(username)
            conn.sendall("ERROR:Incorrect password\n".encode())
            log_security("FAILED_LOGIN", username, client_ip)
            return None
        clear_failures(username)
        log_security("LOGIN_SUCCESS", username, client_ip)
    else:
        with users_lock:
            users_db[username] = {
                "password_hash": pw_hash,
                "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            save_users()
        log_security("USER_REGISTERED", username, client_ip)
        conn.sendall("REGISTERED:Account created\n".encode())

    conn.sendall("AUTH_OK:\n".encode())
    return username

# ---------------- client handler ----------------
def handle_client(conn, addr):
    client_ip = addr[0]
    username = None

    try:
        username = authenticate(conn, addr)
        if not username:
            conn.close()
            return

        login_time = datetime.datetime.now().strftime("%H:%M:%S")
        returning = any(r[1] == username for r in chat_history)

        with clients_lock:
            clients[username] = {
                "conn": conn,
                "ip": client_ip,
                "port": addr[1],
                "login_time": login_time,
                "status": "online"
            }

        log_event("CONNECTED", username, client_ip)
        broadcast(f"[SERVER] {username} joined the chat!\n", sender=username)

        if returning:
            last_msgs = get_last_five(username)
            if last_msgs:
                conn.sendall("=== Your last 5 messages ===\n".encode())
                for msg in last_msgs:
                    conn.sendall(f"[{msg[0]}] {msg[4]}\n".encode())
                conn.sendall("============================\n".encode())

        conn.sendall(
            f"Welcome {username}! Commands:\n"
            f"  /msg <user> <text> → private message\n"
            f"  /list → show online users\n"
            f"  /stats → server statistics\n"
            f"  /logout → log out\n".encode())

        conn.settimeout(INACTIVITY_TIMEOUT)

        while True:
            try:
                data = conn.recv(1024)
            except socket.timeout:
                conn.sendall("[SERVER] Session timed out due to inactivity. Disconnecting...\n".encode())
                log_security("SESSION_TIMEOUT", username, client_ip)
                break

            if not data:
                break

            message = data.decode(errors="ignore").strip()
            if not message:
                continue

            if len(message) > MAX_MESSAGE_LEN:
                conn.sendall(f"[ERROR] Message too long (max {MAX_MESSAGE_LEN} chars)\n".encode())
                log_security("OVERSIZED_MESSAGE_REJECTED", username, client_ip)
                continue

            if message.startswith("/"):
                command = message.split(" ", 1)[0]
                if command not in ALLOWED_COMMANDS:
                    conn.sendall(f"[ERROR] Unsupported command: {command}\n".encode())
                    log_security("UNSUPPORTED_COMMAND", username, client_ip, command)
                    continue

            stats["total_messages"] += 1

            if message.startswith("/msg "):
                parts = message.split(" ", 2)
                if len(parts) >= 3:
                    target = parts[1]
                    private_msg = parts[2]
                    if not send_private(username, target, private_msg):
                        conn.sendall(f"[ERROR] User '{target}' not found!\n".encode())
                else:
                    conn.sendall("[ERROR] Format: /msg <user> <msg>\n".encode())

            elif message == "/list":
                send_user_list(conn)

            elif message == "/stats":
                with clients_lock:
                    online = len(clients)
                conn.sendall((
                    f"=== Server Statistics ===\n"
                    f"Connected Users: {online}\n"
                    f"Total Messages: {stats['total_messages']}\n"
                    f"Broadcast Messages: {stats['broadcast_messages']}\n"
                    f"Private Messages: {stats['private_messages']}\n"
                    f"=========================\n").encode())

            elif message == "/logout":
                conn.sendall("[SERVER] You have been logged out.\n".encode())
                log_security("LOGOUT", username, client_ip)
                break

            else:
                formatted = f"[{username}] {message}\n"
                print(f"[CHAT] {formatted.strip()}")
                broadcast(formatted, sender=username)
                save_history(username, "ALL", "broadcast", message)

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        if username:
            with clients_lock:
                if username in clients:
                    clients[username]["status"] = "offline"
                    del clients[username]
            log_event("DISCONNECTED", username, client_ip)
            broadcast(f"[SERVER] {username} left the chat!\n")
        conn.close()

# ---------------- start server ----------------
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(10)
print(f"[SERVER] Chat server started on port {PORT}")
print("[SERVER] Waiting for clients...")

while True:
    conn, addr = server.accept()
    t = threading.Thread(target=handle_client, args=(conn, addr))
    t.daemon = True
    t.start()
    
