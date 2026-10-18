import os
import socket
import sys
import threading

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
OK = "200\n\n"
FAIL = "500\n\n"
SERVER_SCRIPT = os.path.basename(__file__)

# Username -> Session. Shared between threads, so guarded by a lock.
sessions = {}
sessions_lock = threading.Lock()


# ------------------------------------------------------------
# Per-client session
# ------------------------------------------------------------
class Session:
    """Everything the server tracks about one connected client."""

    def __init__(self, control_sock, address):
        self.control = control_sock
        self.address = address
        self.data = None
        self.username = None
        # Several threads may write to this client's data socket
        # (its own replies + other clients' broadcasts).
        self.send_lock = threading.Lock()

    # --- sending -------------------------------------------------
    def send_data(self, payload):
        """Send text or bytes over the DATA connection (raises OSError on failure)."""
        if isinstance(payload, str):
            payload = payload.encode()
        with self.send_lock:
            self.data.sendall(payload)

    def try_send_data(self, payload):
        """Send to this client but never raise; used when notifying OTHER clients."""
        try:
            self.send_data(payload)
        except OSError:
            pass

    def send_control(self, payload):
        self.control.sendall(payload.encode())

    # --- receiving -----------------------------------------------
    def read_command(self):
        """Read one command from the CONTROL connection. Returns None if closed."""
        raw = self.control.recv(1024)
        if not raw:
            return None
        return raw.decode().strip()

    def read_file_bytes(self):
        """Read an uploaded file from the DATA connection.

        Wait for the first chunk, then keep reading until the sender goes quiet,
        so files larger than one recv() are not truncated.
        """
        self.data.settimeout(None)
        first = self.data.recv(4096)
        if not first:
            raise OSError("data connection closed")
        chunks = [first]
        self.data.settimeout(0.5)
        try:
            while True:
                piece = self.data.recv(4096)
                if not piece:
                    raise OSError("data connection closed")
                chunks.append(piece)
        except socket.timeout:
            pass
        finally:
            self.data.settimeout(None)
        return b"".join(chunks)


def others_snapshot(exclude=None):
    """Copy of the current sessions, optionally leaving one username out."""
    with sessions_lock:
        return [s for name, s in sessions.items() if name != exclude]


# ------------------------------------------------------------
# Connection setup: connect + data socket + login
# ------------------------------------------------------------
def do_connect(sess):
    """Handle 'connect <ip> <port>' and open the data connection."""
    request = sess.read_command()
    if request is None:
        return False

    parts = request.split()
    if len(parts) != 3 or parts[0] != "connect":
        sess.send_control(FAIL)
        return False
    try:
        int(parts[2])
    except ValueError:
        sess.send_control(FAIL)
        return False

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        print("Connection requested. Creating data socket")
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        port = listener.getsockname()[1]
        sess.send_control(f"200\n\n{port}")
        sess.data, _ = listener.accept()
    finally:
        listener.close()
    return True


def do_login(sess):
    """Loop until the client logs in with a unique username."""
    while True:
        request = sess.read_command()
        if request is None:
            return False

        parts = request.split(" ", 1)
        if len(parts) != 2 or parts[0] != "login" or parts[1].strip() == "":
            sess.send_data(FAIL)
            continue

        wanted = parts[1].strip()
        with sessions_lock:
            taken = wanted in sessions
            if not taken:
                sessions[wanted] = sess

        if taken:
            sess.send_data(FAIL)
            continue

        sess.username = wanted
        sess.send_data(OK)
        print(f"Login requested by: {wanted}")

        for other in others_snapshot(exclude=wanted):
            other.try_send_data(f"200\n\njoin\n{wanted}")
        return True


# ------------------------------------------------------------
# Command handlers. Each receives (session, argument_or_None).
# Return True only when the client should be disconnected.
# ------------------------------------------------------------
def cmd_who(sess, arg):
    if arg is not None:
        sess.send_data(FAIL)
        return
    with sessions_lock:
        names = list(sessions.keys())
    print("Who requested. Sending users.")
    sess.send_data("200\n\n" + ", ".join(names))


def cmd_broadcast(sess, arg):
    message = arg.strip() if arg else ""
    if message == "":
        sess.send_data(FAIL)
        return
    print(f"Broadcast requested by {sess.username}")
    print(f"Message: {message}")
    note = f"200\n\nBroadcast\n{sess.username}\n{message}"
    for target in others_snapshot():
        target.try_send_data(note)


def cmd_private(sess, arg):
    pieces = arg.split(" ", 1) if arg else []
    if len(pieces) != 2:
        sess.send_data(FAIL)
        return
    recipient, message = pieces[0].strip(), pieces[1].strip()
    print(f"Private message from {sess.username} to {recipient}")
    if recipient == "" or message == "":
        sess.send_data(FAIL)
        return

    with sessions_lock:
        target = sessions.get(recipient)
    if target is None:
        sess.send_data(FAIL)
        return

    target.try_send_data(f"200\n\nPrivate\n{sess.username}\n{message}")
    sess.send_data(OK)


def safe_name(arg):
    """Strip any directory part so clients can't touch paths outside the server folder."""
    if arg is None:
        return ""
    return os.path.basename(arg.strip())


def cmd_stor(sess, arg):
    name = safe_name(arg)
    if name == "" or name == SERVER_SCRIPT:
        sess.send_data(FAIL)
        return
    print(f"Stor {name} requested by {sess.username}")
    try:
        content = sess.read_file_bytes()
        with open(name, "wb") as fh:
            fh.write(content)
        print("STOR complete")
        sess.send_data(OK)
    except OSError:
        sess.send_data(FAIL)


def cmd_retr(sess, arg):
    name = safe_name(arg)
    if name == "":
        sess.send_data(FAIL)
        return
    try:
        with open(name, "rb") as fh:
            content = fh.read()
    except OSError:
        sess.send_data(FAIL)
        return
    print(f"Retr requested by {sess.username}. Sending file: {name}")
    sess.send_data(b"200\n\n" + content)
    print("File sent.")


def cmd_dele(sess, arg):
    name = safe_name(arg)
    if name == "" or name == SERVER_SCRIPT:
        sess.send_data(FAIL)
        return
    try:
        print(f"Dele requested by {sess.username}. Deleting file: {name}")
        os.remove(name)
        print("Delete complete")
        sess.send_data(OK)
    except OSError:
        sess.send_data(FAIL)


def cmd_list(sess, arg):
    if arg is not None:
        sess.send_data(FAIL)
        return
    names = sorted(
        entry for entry in os.listdir(".")
        if os.path.isfile(entry) and entry != SERVER_SCRIPT
    )
    print(f"List requested by {sess.username}. Sending files.")
    sess.send_data("200\n\n" + ", ".join(names))


def cmd_quit(sess, arg):
    if arg is not None:
        sess.send_data(FAIL)
        return False
    print(f"Quit requested by {sess.username}")
    sess.send_data(OK)
    return True


COMMANDS = {
    "who": cmd_who,
    "broadcast": cmd_broadcast,
    "private": cmd_private,
    "stor": cmd_stor,
    "retr": cmd_retr,
    "dele": cmd_dele,
    "list": cmd_list,
    "quit": cmd_quit,
}


def command_loop(sess):
    while True:
        request = sess.read_command()
        if request is None:
            return
        if request == "":
            sess.send_data(FAIL)
            continue

        pieces = request.split(" ", 1)
        handler = COMMANDS.get(pieces[0])
        if handler is None:
            sess.send_data(FAIL)
            continue

        arg = pieces[1] if len(pieces) == 2 else None
        if handler(sess, arg):
            return


# ------------------------------------------------------------
# Thread entry point + cleanup
# ------------------------------------------------------------
def disconnect(sess):
    """Remove the client, announce the logout, close both sockets."""
    was_logged_in = False
    if sess.username is not None:
        with sessions_lock:
            if sessions.get(sess.username) is sess:
                del sessions[sess.username]
                was_logged_in = True

    if was_logged_in:
        for other in others_snapshot():
            other.try_send_data(f"200\n\nlogout\n{sess.username}")

    for sock in (sess.control, sess.data):
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def serve_client(control_sock, address):
    sess = Session(control_sock, address)
    try:
        if do_connect(sess) and do_login(sess):
            command_loop(sess)
    except OSError:
        print("Client disconnected unexpectedly:", address)
    finally:
        disconnect(sess)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    if len(sys.argv) != 2:
        print("Usage: python server.py <control_port>")
        sys.exit(1)

    port = int(sys.argv[1])
    print("Starting server...")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print("Creating server socket")
    listener.bind(("0.0.0.0", port))
    listener.listen()
    print("Awaiting connections...")

    while True:
        conn, addr = listener.accept()
        threading.Thread(target=serve_client, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()