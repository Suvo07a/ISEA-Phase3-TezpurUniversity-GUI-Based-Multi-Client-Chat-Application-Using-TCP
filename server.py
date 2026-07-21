import socket
import threading
import datetime
import csv
import os
import json
import hashlib
import re
import time
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
import psutil


def load_config():
    default = {
        "host": "0.0.0.0", "port": 5000, "max_message_len": 500,
        "max_failed_attempts": 5, "lockout_seconds": 60,
        "inactivity_timeout": 300, "heartbeat_interval": 15,
        "heartbeat_timeout": 45, "thread_pool_size": 50,
        "listen_backlog": 20
    }
    if os.path.exists("config.json"):
        try:
            with open("config.json") as f:
                loaded = json.load(f).get("server", {})
            default.update(loaded)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[CONFIG] Failed to load config.json, using defaults: {e}")
    return default

CONFIG = load_config()
process = psutil.Process()
HOST = CONFIG["host"]
PORT = CONFIG["port"]
MAX_MESSAGE_LEN = CONFIG["max_message_len"]
MAX_FAILED_ATTEMPTS = CONFIG["max_failed_attempts"]
LOCKOUT_SECONDS = CONFIG["lockout_seconds"]
INACTIVITY_TIMEOUT = CONFIG["inactivity_timeout"]
HEARTBEAT_INTERVAL = CONFIG["heartbeat_interval"]
HEARTBEAT_TIMEOUT = CONFIG["heartbeat_timeout"]
THREAD_POOL_SIZE = CONFIG["thread_pool_size"]
LISTEN_BACKLOG = CONFIG["listen_backlog"]

USERS_FILE = "users.json"
LOG_FILE = "security_log.txt"

USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,20}$')
ALLOWED_COMMANDS = {"/msg", "/list", "/stats", "/logout"}

# {username: {conn, ip, port, login_time, status, last_seen}}
clients = {}
clients_lock = threading.Lock()

stats = {
    "total_messages": 0,
    "broadcast_messages": 0,
    "private_messages": 0,
    "peak_concurrent_clients": 0,
    "total_connections": 0,
    "rejected_connections": 0
}
stats_lock = threading.Lock()

# ---------------- performance metrics support (Task 5) ----------------
ping_sent_at = {}          # username -> time.time() when last PING was sent
latency_samples = []       # list of round-trip delays (seconds) collected since last CSV row
latency_lock = threading.Lock()

chat_history = []
history_lock = threading.Lock()

shutdown_event = threading.Event()

# ---------------- user database ----------------
def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[USERS] Failed to load users.json: {e}")
            return {}
    return {}

def save_users():
    try:
        with open(USERS_FILE, "w") as f:
            json.dump(users_db, f, indent=2)
    except OSError as e:
        print(f"[USERS] Failed to save users.json: {e}")

users_db = load_users()
users_lock = threading.Lock()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ---------------- failed login tracking ----------------
failed_attempts = {}
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

# ---------------- logging ----------------
log_lock = threading.Lock()

def log_security(event, username, ip, detail=""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event} | user={username} | ip={ip} | {detail}\n"
    try:
        with log_lock:
            with open(LOG_FILE, "a") as f:
                f.write(line)
    except OSError as e:
        print(f"[LOG] Failed to write security log: {e}")
    print(f"[SECURITY] {line.strip()}")

def log_event(event, username, ip):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[SERVER] {ts},{event},{username},{ip}")

# ---------------- chat history ----------------
def save_history(sender, receiver, msg_type, message):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    with history_lock:
        chat_history.append([ts, sender, receiver, msg_type, message])
        try:
            with open("chat_history.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["timestamp", "sender", "receiver", "message_type", "message"])
                w.writerows(chat_history)
        except OSError as e:
            print(f"[HISTORY] Failed to write chat_history.csv: {e}")

def get_last_five(username):
    with history_lock:
        user_msgs = [r for r in chat_history if r[1] == username]
        return user_msgs[-5:]

# ---------------- safe send helper (Task 2: exception handling) ----------------
def safe_send(conn, data, username=None, ip=None):
    """Send data, returning False (and logging) on any socket failure instead of crashing."""
    try:
        conn.sendall(data.encode() if isinstance(data, str) else data)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        if username:
            log_security("SEND_FAILED", username, ip or "?", str(e))
        return False

# ---------------- messaging ----------------
def broadcast(message, sender=None):
    dead = []
    with clients_lock:
        items = list(clients.items())
    for uname, info in items:
        if uname != sender:
            if not safe_send(info["conn"], message, uname, info["ip"]):
                dead.append(uname)
    for uname in dead:
        remove_client(uname, reason="broadcast send failure")
    with stats_lock:
        stats["broadcast_messages"] += 1

def send_private(sender, target, message):
    with clients_lock:
        target_info = clients.get(target)
        sender_info = clients.get(sender)
    if not target_info or not sender_info:
        return False
    ok1 = safe_send(target_info["conn"], f"[PRIVATE from {sender}] {message}\n", target, target_info["ip"])
    ok2 = safe_send(sender_info["conn"], f"[PRIVATE to {target}] {message}\n", sender, sender_info["ip"])
    if ok1 and ok2:
        with stats_lock:
            stats["private_messages"] += 1
        save_history(sender, target, "private", message)
        return True
    if not ok1:
        remove_client(target, reason="private send failure")
    return False

def send_user_list(conn, username, ip):
    with clients_lock:
        user_list = list(clients.keys())
    msg = "=== Online Users ===\n"
    for u in user_list:
        msg += f"  → {u}\n"
    msg += "==================\n"
    safe_send(conn, msg, username, ip)

# ---------------- connection management (Task 1) ----------------
def remove_client(username, reason=""):
    """Remove a client, release its socket, and notify the rest of the room."""
    info = None
    with clients_lock:
        info = clients.pop(username, None)
    if info:
        try:
            info["conn"].shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            info["conn"].close()
        log_event("DISCONNECTED", username, info["ip"])
        log_security("CLIENT_REMOVED", username, info["ip"], reason)
        broadcast(f"[SERVER] {username} left the chat!\n")

def heartbeat_monitor():
    """Detects dead/unresponsive clients and cleans them up automatically (Task 1 & 2)."""
    while not shutdown_event.is_set():
        time.sleep(HEARTBEAT_INTERVAL)
        now = time.time()
        with clients_lock:
            snapshot = list(clients.items())
        for uname, info in snapshot:
            if now - info.get("last_seen", now) > HEARTBEAT_TIMEOUT:
                remove_client(uname, reason="heartbeat timeout - no response")
                continue
            with latency_lock:
                ping_sent_at[uname] = now
            safe_send(info["conn"], "PING\n", uname, info["ip"])

# ---------------- performance logging (Task 5) ----------------
def log_performance():
    """Samples real CPU%, memory, concurrent client count, message throughput and
    average heartbeat round-trip delay roughly once a second and appends them to
    performance_results.csv for before/after comparison graphs."""
    last_total_messages = 0
    with open("performance_results.csv", "a", newline="") as f:
        w = csv.writer(f)
        if f.tell() == 0:
            w.writerow(["timestamp", "connected_clients", "cpu_percent", "memory_mb",
                        "throughput_msgs_per_sec", "avg_delay_ms"])
        while not shutdown_event.is_set():
            with clients_lock:
                n = len(clients)
            cpu = process.cpu_percent(interval=1)  # blocks ~1s, doubles as the sleep
            mem = round(process.memory_info().rss / (1024 * 1024), 2)

            with stats_lock:
                total_now = stats["total_messages"]
            throughput = total_now - last_total_messages
            last_total_messages = total_now

            with latency_lock:
                samples = latency_samples.copy()
                latency_samples.clear()
            avg_delay_ms = round((sum(samples) / len(samples)) * 1000, 2) if samples else ""

            w.writerow([datetime.datetime.now().strftime("%H:%M:%S"), n, cpu, mem,
                        throughput, avg_delay_ms])
            f.flush()

# ---------------- authentication ----------------
def authenticate(conn, addr):
    client_ip = addr[0]
    if not safe_send(conn, "USERNAME:\n"):
        return None
    try:
        raw = conn.recv(1024)
    except (socket.timeout, OSError):
        return None
    if not raw:
        return None
    username = raw.decode(errors="ignore").strip()

    if not USERNAME_RE.match(username):
        safe_send(conn, "ERROR:Invalid username (3-20 letters/numbers/underscore only)\n")
        log_security("INVALID_USERNAME", username or "?", client_ip)
        return None

    with clients_lock:
        if username in clients:
            safe_send(conn, "ERROR:User already logged in elsewhere\n")
            log_security("DUPLICATE_LOGIN_BLOCKED", username, client_ip)
            return None

    locked, remaining = is_locked(username)
    if locked:
        safe_send(conn, f"ERROR:Account locked. Try again in {remaining}s\n")
        log_security("LOGIN_BLOCKED_LOCKOUT", username, client_ip)
        return None

    with users_lock:
        exists = username in users_db

    safe_send(conn, "PASSWORD:\n" if exists else "NEW_PASSWORD:\n")

    try:
        raw_pw = conn.recv(1024)
    except (socket.timeout, OSError):
        return None
    if not raw_pw:
        return None
    password = raw_pw.decode(errors="ignore").strip()

    if not password:
        safe_send(conn, "ERROR:Password cannot be empty\n")
        log_security("EMPTY_PASSWORD", username, client_ip)
        return None

    pw_hash = hash_password(password)

    if exists:
        with users_lock:
            stored_hash = users_db[username]["password_hash"]
        if pw_hash != stored_hash:
            register_failure(username)
            safe_send(conn, "ERROR:Incorrect password\n")
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
        safe_send(conn, "REGISTERED:Account created\n")

    safe_send(conn, "AUTH_OK:\n")
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
                "conn": conn, "ip": client_ip, "port": addr[1],
                "login_time": login_time, "status": "online",
                "last_seen": time.time()
            }
        with stats_lock:
            stats["total_connections"] += 1
            stats["peak_concurrent_clients"] = max(stats["peak_concurrent_clients"], len(clients))

        log_event("CONNECTED", username, client_ip)
        broadcast(f"[SERVER] {username} joined the chat!\n", sender=username)

        if returning:
            last_msgs = get_last_five(username)
            if last_msgs:
                safe_send(conn, "=== Your last 5 messages ===\n", username, client_ip)
                for msg in last_msgs:
                    safe_send(conn, f"[{msg[0]}] {msg[4]}\n", username, client_ip)
                safe_send(conn, "============================\n", username, client_ip)

        safe_send(conn,
            f"Welcome {username}! Commands:\n"
            f"  /msg <user> <text> → private message\n"
            f"  /list → show online users\n"
            f"  /stats → server statistics\n"
            f"  /logout → log out\n", username, client_ip)

        conn.settimeout(INACTIVITY_TIMEOUT)

        while not shutdown_event.is_set():
            try:
                data = conn.recv(1024)
            except socket.timeout:
                safe_send(conn, "[SERVER] Session timed out due to inactivity. Disconnecting...\n", username, client_ip)
                log_security("SESSION_TIMEOUT", username, client_ip)
                break
            except (ConnectionResetError, OSError) as e:
                log_security("CONNECTION_ERROR", username, client_ip, str(e))
                break

            if not data:
                break

            message = data.decode(errors="ignore").strip()
            if not message:
                continue

            if message == "PONG":
                with clients_lock:
                    if username in clients:
                        clients[username]["last_seen"] = time.time()
                with latency_lock:
                    sent_at = ping_sent_at.pop(username, None)
                    if sent_at is not None:
                        latency_samples.append(time.time() - sent_at)
                continue

            with clients_lock:
                if username in clients:
                    clients[username]["last_seen"] = time.time()

            if len(message) > MAX_MESSAGE_LEN:
                safe_send(conn, f"[ERROR] Message too long (max {MAX_MESSAGE_LEN} chars)\n", username, client_ip)
                log_security("OVERSIZED_MESSAGE_REJECTED", username, client_ip)
                continue

            if message.startswith("/"):
                command = message.split(" ", 1)[0]
                if command not in ALLOWED_COMMANDS:
                    safe_send(conn, f"[ERROR] Unsupported command: {command}\n", username, client_ip)
                    log_security("UNSUPPORTED_COMMAND", username, client_ip, command)
                    continue

            with stats_lock:
                stats["total_messages"] += 1

            if message.startswith("/msg "):
                parts = message.split(" ", 2)
                if len(parts) >= 3:
                    target, private_msg = parts[1], parts[2]
                    if not send_private(username, target, private_msg):
                        safe_send(conn, f"[ERROR] User '{target}' not found!\n", username, client_ip)
                else:
                    safe_send(conn, "[ERROR] Format: /msg <user> <msg>\n", username, client_ip)

            elif message == "/list":
                send_user_list(conn, username, client_ip)

            elif message == "/stats":
                with clients_lock:
                    online = len(clients)
                with stats_lock:
                    s = dict(stats)
                safe_send(conn, (
                    f"=== Server Statistics ===\n"
                    f"Connected Users: {online}\n"
                    f"Total Messages: {s['total_messages']}\n"
                    f"Broadcast Messages: {s['broadcast_messages']}\n"
                    f"Private Messages: {s['private_messages']}\n"
                    f"Peak Concurrent Clients: {s['peak_concurrent_clients']}\n"
                    f"=========================\n"), username, client_ip)

            elif message == "/logout":
                safe_send(conn, "[SERVER] You have been logged out.\n", username, client_ip)
                log_security("LOGOUT", username, client_ip)
                break

            else:
                formatted = f"[{username}] {message}\n"
                print(f"[CHAT] {formatted.strip()}")
                broadcast(formatted, sender=username)
                save_history(username, "ALL", "broadcast", message)

    except (OSError, ConnectionError) as e:
        log_security("HANDLER_ERROR", username or "?", client_ip, str(e))
    except Exception as e:
        # last-resort catch so one bad client can never take the whole server down
        log_security("UNEXPECTED_ERROR", username or "?", client_ip, str(e))
    finally:
        if username:
            remove_client(username, reason="handler exit")
        else:
            try:
                conn.close()
            except OSError:
                pass

# ---------------- graceful shutdown (Task 2) ----------------
def shutdown_server(server_sock, executor):
    print("\n[SERVER] Shutdown signal received. Closing connections...")
    shutdown_event.set()
    with clients_lock:
        usernames = list(clients.keys())
    for uname in usernames:
        info = clients.get(uname)
        if info:
            safe_send(info["conn"], "[SERVER] Server is shutting down. Goodbye!\n", uname, info["ip"])
        remove_client(uname, reason="server shutdown")
    try:
        server_sock.close()
    except OSError:
        pass
    executor.shutdown(wait=True, cancel_futures=True)
    print("[SERVER] Shutdown complete.")
    sys.exit(0)

# ---------------- start server (Task 3: scalability via thread pool) ----------------
def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(LISTEN_BACKLOG)
    server_sock.settimeout(1.0)  # lets the accept loop check shutdown_event periodically

    executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE, thread_name_prefix="client")

    signal.signal(signal.SIGINT, lambda sig, frame: shutdown_server(server_sock, executor))
    signal.signal(signal.SIGTERM, lambda sig, frame: shutdown_server(server_sock, executor))

    threading.Thread(target=heartbeat_monitor, daemon=True).start()
    threading.Thread(target=log_performance, daemon=True).start()

    print(f"[SERVER] Chat server started on {HOST}:{PORT}")
    print(f"[SERVER] Thread pool size: {THREAD_POOL_SIZE}, backlog: {LISTEN_BACKLOG}")
    print("[SERVER] Waiting for clients...")

    while not shutdown_event.is_set():
        try:
            conn, addr = server_sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        with clients_lock:
            current_load = len(clients)

        if current_load >= THREAD_POOL_SIZE:
            safe_send(conn, "ERROR:Server at capacity, try again shortly\n")
            with stats_lock:
                stats["rejected_connections"] += 1
            log_security("CONNECTION_REJECTED", "?", addr[0], "server at capacity")
            conn.close()
            continue

        executor.submit(handle_client, conn, addr)

    executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
