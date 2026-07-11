# ISEA-Phase3-TezpurUniversity-Assignment6

## Project Title
GUI-Based Multi-Client Chat Application Using TCP

## Objective
Convert the terminal-based TCP chat application built in Assignment 5 into a graphical desktop
application using Python's Tkinter library, while reusing the existing Assignment 5 server
implementation without modification. The project demonstrates GUI programming, event-driven
design, multithreaded network I/O, and a responsive user interface layered on top of an existing
socket-based chat protocol.

## Software Requirements
- Python 3
- Kali Linux / Ubuntu with Mininet installed
- Tkinter (`python3-tk`)
- Wireshark (for packet capture verification)

## Network Topology
Emulated using Mininet's `single,5` topology — one server host and four client hosts connected
through a single Open vSwitch.

```
h1 : Chat Server
h2 : Client A
h3 : Client B
h4 : Client C
h5 : Client D
```

```
sudo mn --topo single,5
```

Verified with `nodes`, `net`, and `pingall` (0% packet loss across all five hosts — see
`screenshots/nodes_net_pingall.png`).

## Execution Steps

1. Clone this repository onto the Kali/Ubuntu host and start Mininet:
   ```
   sudo mn --topo single,5
   ```

2. Open terminals for each host:
   ```
   mininet> xterm h1 h2 h3 h4 h5
   ```

3. On **h1**, start the server:
   ```
   cd <repo-folder>
   python3 server.py
   ```

4. On **h2, h3, h4, h5**, start the GUI client:
   ```
   cd <repo-folder>
   python3 client_gui.py
   ```

5. In the login window on each client, enter the server IP (`10.0.0.1`) and a username, then click
   **Connect**.

6. Use the chat window to send broadcast messages, private messages (check **Private** and select
   a user from the online list), and disconnect using the **Disconnect** button.

## Sample Screenshots

| Scenario | Screenshot |
|---|---|
| Login window | `screenshots/login_window.png` |
| Successful connection | `screenshots/successful_connection.png` |
| Main chat window | `screenshots/main_chat_window.png` |
| Broadcast messaging | `screenshots/broadcast_message.png` |
| Private messaging | `screenshots/private_message.png` |
| User joining | `screenshots/user_join.png` |
| User leaving | `screenshots/user_leave.png` |
| Client disconnect | `screenshots/user_disconnect.png` |
| Mininet topology verification | `screenshots/nodes_net_pingall.png` |
| Wireshark — client connection | `screenshots/client_connection.png` |
| Wireshark — broadcast message | `screenshots/broadcast_message_wireshark.png` |
| Wireshark — private message | `screenshots/private_message_wireshark.png` |
| Wireshark — client disconnection | `screenshots/client_disconnection_wireshark.png` |

## Brief Description of Implementation

- **server.py** — reused unmodified from Assignment 5. A multi-threaded TCP server on port 5000
  handles client registration, broadcast messaging, private messaging (`/msg`), the online user
  list (`/list`), server statistics (`/stats`), and persistent chat history with reconnect replay
  (`chat_history.csv`).
- **client_gui.py** — a new Tkinter GUI client that reuses the exact same socket protocol as the
  Assignment 5 terminal client. Networking logic (`ChatClientNetwork`) is kept fully separate from
  the GUI (`LoginWindow`, `ChatWindow`). A background daemon thread performs the blocking
  `sock.recv()` calls and hands incoming data to the GUI thread via a thread-safe queue, so the
  interface never freezes while waiting for messages.
- Verified end-to-end inside Mininet with four simultaneous clients; all message flows were
  cross-checked against `chat_history.csv` and Wireshark packet captures filtered on
  `tcp.port == 5000`.

## Report
See `report.pdf` for the full report, including system architecture, GUI design decisions, testing
results, Wireshark verification, and reflection answers.
