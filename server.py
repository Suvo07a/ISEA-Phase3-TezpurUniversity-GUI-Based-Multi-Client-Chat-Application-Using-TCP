import socket
import threading
import datetime
import csv
import os

HOST = '0.0.0.0'
PORT = 5000

# Client info dictionary
# {username: {conn, ip, port, login_time, status}}
clients = {}
clients_lock = threading.Lock()

# Server statistics
stats = {
    "total_messages": 0,
    "broadcast_messages": 0,
    "private_messages": 0
}

# Chat history
chat_history = []

def save_history(sender, receiver, msg_type, message):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    chat_history.append([ts, sender, receiver, msg_type, message])
    with open("chat_history.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp","sender","receiver",
                    "message_type","message"])
        w.writerows(chat_history)

def get_last_five(username):
    user_msgs = [r for r in chat_history
                 if r[1] == username]
    return user_msgs[-5:]

def log_event(event, username, ip):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[SERVER] {ts},{event},{username},{ip}")

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
                # Send confirmation to sender
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

def handle_client(conn, addr):
    client_ip = addr[0]
    client_port = addr[1]
    username = None

    try:
        # Send username prompt
        conn.sendall("Enter Username:\n".encode())
        username = conn.recv(1024).decode().strip()

        login_time = datetime.datetime.now().strftime("%H:%M:%S")

        # Check if returning user
        returning = any(r[1] == username for r in chat_history)

        with clients_lock:
            clients[username] = {
                "conn": conn,
                "ip": client_ip,
                "port": client_port,
                "login_time": login_time,
                "status": "online"
            }

        log_event("CONNECTED", username, client_ip)
        broadcast(f"[SERVER] {username} joined the chat!\n",
                  sender=username)

        # Show last 5 messages if returning user
        if returning:
            last_msgs = get_last_five(username)
            if last_msgs:
                conn.sendall(
                    "=== Your last 5 messages ===\n".encode())
                for msg in last_msgs:
                    conn.sendall(
                        f"[{msg[0]}] {msg[4]}\n".encode())
                conn.sendall(
                    "============================\n".encode())

        # Welcome message
        conn.sendall(
            f"Welcome {username}! Commands:\n"
            f"  /msg <user> <text> → private message\n"
            f"  /list → show online users\n"
            f"  /stats → server statistics\n".encode())

        while True:
            data = conn.recv(1024)
            if not data:
                break

            message = data.decode().strip()
            stats["total_messages"] += 1

            # Private message
            if message.startswith("/msg "):
                parts = message.split(" ", 2)
                if len(parts) >= 3:
                    target = parts[1]
                    private_msg = parts[2]
                    if not send_private(username, target,
                                        private_msg):
                        conn.sendall(
                            f"[ERROR] User '{target}' not found!\n"
                            .encode())
                else:
                    conn.sendall(
                        "[ERROR] Format: /msg <user> <msg>\n"
                        .encode())

            # List users
            elif message == "/list":
                send_user_list(conn)

            # Server stats
            elif message == "/stats":
                with clients_lock:
                    online = len(clients)
                stat_msg = (
                    f"=== Server Statistics ===\n"
                    f"Connected Users: {online}\n"
                    f"Total Messages: {stats['total_messages']}\n"
                    f"Broadcast Messages: {stats['broadcast_messages']}\n"
                    f"Private Messages: {stats['private_messages']}\n"
                    f"=========================\n"
                )
                conn.sendall(stat_msg.encode())

            # Broadcast message
            else:
                formatted = f"[{username}] {message}\n"
                print(f"[CHAT] {formatted.strip()}")
                broadcast(formatted, sender=username)
                save_history(username, "ALL",
                             "broadcast", message)

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        if username:
            with clients_lock:
                if username in clients:
                    clients[username]["status"] = "offline"
                    del clients[username]
            log_event("DISCONNECTED", username, client_ip)
            broadcast(
                f"[SERVER] {username} left the chat!\n")
        conn.close()

# Start server
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(10)
print(f"[SERVER] Chat server started on port {PORT}")
print("[SERVER] Waiting for clients...")

while True:
    conn, addr = server.accept()
    t = threading.Thread(target=handle_client,
                         args=(conn, addr))
    t.daemon = True
    t.start()
