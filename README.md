# Secure Multi-Client TCP Chat Application

A GUI-based multi-client chat system built with Python sockets and Tkinter, extended with practical application-layer security features.



## Overview

This project started in Assignment 6 as a basic multi-client TCP chat application (broadcast messages, private messages, online user list, chat history). Assignment 7 extends it with authentication, secure password storage, session management, and input validation — without rewriting the core networking or GUI logic.

## Features

### Core Chat
- Multi-client TCP server using threading
- GUI client built with Tkinter
- Broadcast and private (/msg) messaging
- Online user list (/list)
- Server statistics (/stats)
- Per-user chat history (last 5 messages shown on reconnect)

### Security Features
- User Authentication — username/password required to join
- Secure Password Storage — SHA-256 hashing, no plaintext passwords ever stored or logged
- Duplicate Login Prevention — blocks a username from being used in two sessions at once
- Input Validation
  - Username: 3–20 characters, letters/numbers/underscore only
  - Password: cannot be empty
  - Messages: max 500 characters
  - Commands: only /msg, /list, /stats, /logout accepted
- Failed Login Protection — 5 consecutive failed attempts triggers a 60-second lockout
- Session Management — 5-minute inactivity timeout, explicit /logout command
- Secure Logging — all security events timestamped in security_log.txt, passwords never logged

## Project Structure

- server.py — TCP server with authentication and security logic
- client_gui.py — Tkinter GUI client
- users.json — User credential store (SHA-256 hashes only)
- security_log.txt — Security event log
- chat_history.csv — Persisted chat history
- report.pdf — Full assignment report
- handwritten_reflection.pdf — Handwritten reflection answers
- screenshots/ — Test evidence for every security feature

## How to Run

1. Set up the network (tested on Mininet, single-switch 5-host topology):

   sudo mn --topo single,5

2. Start the server:

   python3 server.py

3. Start one or more clients:

   python3 client_gui.py

4. In the login window, enter the server IP, a username, and a password. New usernames are registered automatically on first login.

## Security Notes

- Passwords are hashed with hashlib.sha256() before being stored — plaintext is never written to disk or to the log file.
- security_log.txt records authentication and session events (logins, failures, lockouts, timeouts, logouts) but never records password values.
- This project focuses on application-level security controls. The TCP channel itself is not encrypted — Wireshark captures (see report.pdf) show this clearly and it's noted as a suggested future improvement (e.g. adding TLS).

## Testing & Verification

All security features were tested against a live server running in Mininet and verified two ways:
1. Through the GUI client (see screenshots/)
2. At the packet level using Wireshark, filtering on tcp.port == 5000 and following individual TCP streams

Full test documentation, architecture details, and Wireshark analysis are in report.pdf.
