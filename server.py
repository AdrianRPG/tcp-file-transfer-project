import socket # Python's built-in library for socket/network programming.
import sys # Python's library that provides access to command-line arguments.
import threading # Python's library that lets multiple sections of program execute concurrently.
import os # Python's library to obtain file names from directories.
from cryptography.hazmat.primitives.asymmetric import rsa # library.cryptographyoperations.cryptographytype - module
from cryptography.hazmat.primitives import serialization # module to convert cryptographic keys into a
# standardized byte representation to send over socket. 
from cryptography.hazmat.primitives.asymmetric import padding # padding methods for the server to decrypt encrypted blocks
from cryptography.hazmat.primitives import hashes # hash algorithm --> OAEP - server must use the same parameters
import hashlib # hashing library to calculate a SHA-256 digest

RSA_CIPHERTEXT_BLOCK_SIZE = 256 # Every RSA-encrypted chunk produced by client has 256 bytes
RSA_PLAINTEXT_CHUNK_SIZE = 190

# ------------------------------------------------------------
# READ THE CONTROL PORT FROM THE COMMAND LINE
# ------------------------------------------------------------
# Check command formatting is good
if len(sys.argv) != 2:
    print("Usage: python server.py <control_port>")
    sys.exit(1)

# Command-line arguments are received as strings.
control_port = int(sys.argv[1]) # Therefore, convert the string argument into an integer.

print("Starting server...")
print("Creating RSA keypair")

# ------------------------------------------------------------
# CREATE THE SERVER'S RSA PRIVATE KEY
# ------------------------------------------------------------
# Store private key generated in memory to decrypt messages that clients encrypt.
server_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) # cryptographic object
# key_size --> creates a 2048-bit RSA key

# ------------------------------------------------------------------
# CREATE THE SERVER'S RSA PUBLIC KEY using the GENERATED PRIVATE KEY
# ------------------------------------------------------------------
# Extracts the public key that belongs to the private key just produced.
server_public_key = server_private_key.public_key() # cryptographic object
# It can be shared with clients so they can encrypt their messages for the server.

# Python Object --> Standardized PEM format byte representation (Standard Structure)
server_public_key_bytes = server_public_key.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)

print("RSA keypair created")

# ------------------------------------------------------------
# CREATE THE CONTROL LISTENING SOCKET
# ------------------------------------------------------------
# This socket will only be used to LISTEN for new connections.

# Ask the OS to create an IPv4 TCP socket (networking endpoint).
control_listening_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# Arguments: AF_INET --→ IPv4; SOCK_STREAM --→ TCP
# Socket does not yet have the server's IP address and TCP control_port.
print("Creating server socket")

# ------------------------------------------------------------
# BIND THE CONTROL LISTENING SOCKET
# ------------------------------------------------------------
# Associate the control listening socket with the server's IP address and TCP control_port.
control_listening_socket.bind(("0.0.0.0", control_port))
# 0.0.0.0 --> Listen for connections on all available IPv4 network interfaces.

# ------------------------------------------------------------
# START LISTENING FOR CONTROL CONNECTIONS
# ------------------------------------------------------------
# Put the control socket into listening mode.
# The socket is now waiting for incoming TCP control connection requests from clients.
control_listening_socket.listen()
print("Awaiting connections...")

# Stores all currently logged-in clients in a Python dictionary.
# USERNAME -> CLIENT INFORMATION (Control Socket, Data Socket)
active_clients = {} # No users logged in yet.

# Dictionary's protection for race condition when multiple threads
# try to modify it at the same time.
active_clients_lock = threading.Lock()

def encrypt_with_public_key(message_bytes, public_key):
    encrypted_message = b"" # empty buffer to accumulate all RSA-blocks for the outgoing message
    for start in range(0, len(message_bytes), RSA_PLAINTEXT_CHUNK_SIZE):
        chunk = message_bytes[start:start + RSA_PLAINTEXT_CHUNK_SIZE]
        encrypted_chunk = public_key.encrypt(chunk, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        encrypted_message += encrypted_chunk
    return encrypted_message    

def decrypt_with_private_key(encrypted_message, private_key):
    decrypted_message = b""
    for start in range(0, len(encrypted_message), RSA_CIPHERTEXT_BLOCK_SIZE):
        encrypted_block = encrypted_message[start:start + RSA_CIPHERTEXT_BLOCK_SIZE]
        decrypted_chunk = private_key.decrypt(encrypted_block, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        decrypted_message += decrypted_chunk
    return decrypted_message

# ------------------------------------------------------------
# CLEAN UP A CLIENT
# ------------------------------------------------------------

def cleanup_client(client_username,control_connection_socket,data_connection_socket):
    # Indicates whether this client had actually completed LOGIN.
    was_active = False
    # Snapshot of clients that remain logged in.
    clients_snapshot = []

    # --------------------------------------------------------
    # REMOVE CLIENT FROM ACTIVE CLIENTS
    # --------------------------------------------------------
    if client_username is not None:
        with active_clients_lock:
            # Only remove the user if they are still registered.
            if client_username in active_clients:
                active_clients.pop(client_username)
                was_active = True
                # Take a snapshot AFTER removing the disconnected user.
                clients_snapshot = list(active_clients.values())

    # --------------------------------------------------------
    # BROADCAST LOGOUT NOTIFICATION
    # --------------------------------------------------------
    # Only broadcast logout if the client had successfully
    # logged in and was registered in active_clients.
    if was_active:
        logout_notification = f"200\n\nlogout\n{client_username}"

        # Send the logout notification to every remaining client.
        for client_info in clients_snapshot:
            try:
                encrypted_logout_notification = encrypt_with_public_key(logout_notification.encode(), client_info["public_key"]) 
                client_info["data_socket"].sendall(encrypted_logout_notification)

            except OSError:
                pass

    # --------------------------------------------------------
    # CLOSE THIS CLIENT'S CONTROL SOCKET
    # --------------------------------------------------------
    try:
        control_connection_socket.close()
    except OSError:
        pass

    # --------------------------------------------------------
    # CLOSE THIS CLIENT'S DATA SOCKET
    # --------------------------------------------------------
    if data_connection_socket is not None:
        try:
            data_connection_socket.close()
        except OSError:
            pass

# ------------------------------------------------------------
# HANDLE PROCESS FOR EACH CLIENT (CONCURRENTLY)
# ------------------------------------------------------------

def handle_client(control_connection_socket, client_control_address):

    client_username = None
    data_connection_socket = None
    data_listening_socket = None

    try:
        
        # ------------------------------------------------------------
        # RECEIVE AND VALIDATE CONNECT COMMAND
        # ------------------------------------------------------------
        # Wait for the client to send the CONNECT command through
        # the already-established CONTROL connection.
        connect_request_bytes = control_connection_socket.recv(1024)

        # recv() returning "blank" means the client closed the connection.
        if not connect_request_bytes:
            return

        # Convert the received bytes into a string
        connect_request = connect_request_bytes.decode().strip()

        # Format: connect <ip_address> <control_port>
        connect_parts = connect_request.split()

        # CONNECT must contain exactly three parts and begin with "connect".
        if len(connect_parts) != 3 or connect_parts[0] != "connect":
            control_connection_socket.sendall("500\n\n".encode())
            return

        # The third argument must be a valid integer TCP port.
        try:
            int(connect_parts[2])
        except ValueError:
            control_connection_socket.sendall("500\n\n".encode())
            return
        
        # ------------------------------------------------------------
        # CREATE THE DATA LISTENING SOCKET
        # ------------------------------------------------------------
        # Ask the OS to create a new TCP socket (networking endpoint) for the server.
        # This will listen for incoming TCP data connection requests from clients.
        data_listening_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        print("Connection requested. Creating data socket")

        # ------------------------------------------------------------
        # BIND THE DATA LISTENING SOCKET
        # ------------------------------------------------------------
        # Associates the data socket with the server's IP address.
        # The server asks the OS for any available TCP port.
        data_listening_socket.bind(("0.0.0.0", 0))

        # ------------------------------------------------------------
        # START LISTENING FOR THE DATA CONNECTION
        # ------------------------------------------------------------
        data_listening_socket.listen()

        # ------------------------------------------------------------
        # FIND THE DATA PORT SELECTED BY THE OS
        # ------------------------------------------------------------
        # Use getsockname() to get the information address (IP[0] & port[1]) of the socket.
        data_port = data_listening_socket.getsockname()[1]

        # ------------------------------------------------------------
        # SEND THE DATA PORT TO THE CLIENT
        # ------------------------------------------------------------
        # The server needs to send the data port through the already
        # established control connection sockets AND its OWN PUBLIC KEY.
        response = b"200\n\n" + str(data_port).encode() + b"\n" + server_public_key_bytes
        control_connection_socket.sendall(response)

        # ------------------------------------------------------------
        # ACCEPT THE CLIENT'S DATA CONNECTION
        # ------------------------------------------------------------
        # The client will create a second TCP data socket and connect it to
        # the data port that the server just sent.
        # accept() then returns a NEW connected data socket for the server dedicated to
        # the data connection.
        data_connection_socket, client_data_address = data_listening_socket.accept()
        # The listening socket is no longer needed after this client's
        # data connection is established.
        data_listening_socket.close() # Close temporary socket.
        data_listening_socket = None

        # ------------------------------------------------------------
        # RECEIVE LOGIN COMMAND
        # ------------------------------------------------------------
        while True:
            # The server receives the login command through the control TCP connection.
            login_request_bytes = b"" # creating empty buffer to fill it with the complete 256 encrypted RSA blocks
            login_plaintext_bytes = b"" # creating empty buffer to fill it with the decrypted login data
            while b"-----END PUBLIC KEY-----" not in login_plaintext_bytes:
                # login plaintext client constructed ends with PEM public key
                encrypted_block = b"" # Empty buffer for complete RSA ciphertext block
                # keep receiving bytes until encrypted block contains one complete RSA of 256
                while len(encrypted_block) < RSA_CIPHERTEXT_BLOCK_SIZE:
                    # receive only the # of bytes still missing to complete one 256-byte RSA
                    block_piece = control_connection_socket.recv(RSA_CIPHERTEXT_BLOCK_SIZE - len(encrypted_block))
                    if not block_piece:
                        return # if client disconnected before server received a complete RSA block, stop processing.
                    encrypted_block += block_piece

                # use server private key to decrypt ciphertext block client encrypted with server public key.
                decrypted_chunk = server_private_key.decrypt(encrypted_block, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))

                # append plaintext piece to full reconstructed message
                login_plaintext_bytes += decrypted_chunk

            print("Received encrypted message")
            
            # Convert the received bytes into a Python string.
            login_request = login_plaintext_bytes.decode().strip()

            # ------------------------------------------------------------
            # EXTRACT THE CLIENT'S USERNAME
            # ------------------------------------------------------------
            # The login command format is:
            # login 
            # 
            # username
            # <public key>
            # Split the string using " " as the separator but only once.
            login_parts = login_request.split("\n\n", 1)

            # A valid login command must contain: login <username>
            if len(login_parts) != 2:
                data_connection_socket.sendall("500\n\n".encode())
                continue

            command = login_parts[0]
            # detect missing separator or failure for any components of expected message
            requested_username, separator, client_public_key_pem = login_parts[1].partition("\n", 1)
            requested_username = requested_username.strip() # removes any whitespace around the username

            # The command must be "login", and username cannot be empty.
            # There must be a newline separating the username from the public key and
            # the client public key cannot be missing or blank
            if command != "login" or requested_username == "" or separator == "" or client_public_key_pem.strip() == "":
                data_connection_socket.sendall("500\n\n".encode())
                continue
            # Converts the PEM text back into bytes to reconstruct the actual cryptographic public key object.     
            client_public_key = serialization.load_pem_public_key(client_public_key_pem.encode())    

            # ------------------------------------------------------------------
            # VALIDATE USERNAME UNIQUENESS
            # ------------------------------------------------------------------
            with active_clients_lock:
                if requested_username in active_clients:
                # Username is already being used.
                    username_available = False
                else:
                    # Register the newly logged-in client with their public key
                    active_clients[requested_username] = {"control_socket": control_connection_socket,
                        "data_socket": data_connection_socket, "public_key": client_public_key}
                    username_available = True

            # Username already exists.
            if not username_available:
                login_response = "500\n\n"
                encrypted_login_response = encrypt_with_public_key(login_response.encode(), client_public_key)
                # Send the login response through the data TCP connection.
                data_connection_socket.sendall(encrypted_login_response)
                # Stop processing THIS login request.
                # Go back to the beginning of the login loop.
                continue

            # Username is unique, and login was successful.
            client_username = requested_username
            login_response = "200\n\n"
            encrypted_login_response = encrypt_with_public_key(login_response.encode(), client_public_key)
            data_connection_socket.sendall(encrypted_login_response)
            print(f"Login requested by: {client_username}")

            # --------------------------------------------------------
            # BROADCAST JOIN NOTIFICATION
            # --------------------------------------------------------
            join_notification = f"200\n\njoin\n{client_username}"

            with active_clients_lock:
                # values() provides the items for each key (a special dictionary view).
                # Therefore, we have to list them.
                clients_snapshot = [client_info for username,
                client_info in active_clients.items()
                    if username != client_username]

            # Application-level broadcasting
            for client_info in clients_snapshot:
                encrypted_join_notification = encrypt_with_public_key(join_notification.encode(), client_info["public_key"])
                client_info["data_socket"].sendall(encrypted_join_notification)

            # Exit the login loop and proceed to normal commands.
            break

        # --------------------------------------------------------
        # RECEIVE AND PARSE COMMAND LOOP
        # --------------------------------------------------------
        while True:
            # Wait for the next encrypted command through the CONTROL connection.
            request_bytes = b""
            request_piece = control_connection_socket.recv(4096)
            if not request_piece:
                break
            request_bytes += request_piece
            decrypted_request_bytes = decrypt_with_private_key(request_bytes, server_private_key)
            print("Received encrypted message")
            message_hash = hashlib.sha256(decrypted_request_bytes).digest()
            
            # Convert the received bytes into a string.
            request = decrypted_request_bytes.decode().strip()

            if request == "":
                empty_request_response = "500\n\n"
                encrypted_empty_request_response = encrypt_with_public_key(empty_request_response.encode(), client_public_key)
                data_connection_socket.sendall(encrypted_empty_request_response)
                continue

            # Split the string using " " as the separator but only once.
            command_parts = request.split(" ", 1)
            command = command_parts[0]

            # ----------------------------------------------------
            # WHO COMMAND
            # ----------------------------------------------------
            if command == "who":

                if len(command_parts) != 1:
                    who_error_response = "500\n\n"
                    encrypted_who_error_response = encrypt_with_public_key(who_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_who_error_response)
                    continue

                # Take a snapshot of the currently logged-in usernames.
                with active_clients_lock:
                    current_users = list(active_clients.keys())

                # Convert:["alice", "bob", "charlie"]
                # into: "alice,bob,charlie"
                users_list = ", ".join(current_users)

                print("Who requested. Sending users.")

                # Construct the WHO response.
                who_response = f"200\n\n{users_list}"
                encrypted_who_response = encrypt_with_public_key(who_response.encode(), client_public_key)

                # Send the response only to the client who requested the command WHO.
                data_connection_socket.sendall(encrypted_who_response)

            # ----------------------------------------------------
            # BROADCAST COMMAND
            # ----------------------------------------------------
            elif command == "broadcast":
                if len(command_parts) != 2:
                    broadcast_error_response = "500\n\n"
                    encrypted_broadcast_error_response = encrypt_with_public_key(broadcast_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_broadcast_error_response)
                    continue
                # Extract the message after the word "broadcast".
                message = command_parts[1].strip()

                if message == "":
                    broadcast_error_response = "500\n\n"
                    encrypted_broadcast_error_response = encrypt_with_public_key(broadcast_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_broadcast_error_response)
                    continue

                print(f"Broadcast requested by {client_username}")
                print(f"Message: {message}")

                # Already know who sent the command because this thread is for a particular client:
                # Construct the message that every active client receives.
                broadcast_response = f"200\n\nBroadcast\n{client_username}\n{message}"
                with active_clients_lock:
                    # information of all clients (data sockets)
                    clients_snapshot = list(active_clients.values())
                # broadcast: send the message to every client's data socket.
                for client_info in clients_snapshot:
                    encrypted_broadcast_response = encrypt_with_public_key(broadcast_response.encode(), client_info["public_key"])
                    client_info["data_socket"].sendall(encrypted_broadcast_response)

            # ----------------------------------------------------
            # PRIVATE COMMAND
            # ----------------------------------------------------
            elif command == "private":
                if len(command_parts) != 2:
                    private_error_response = "500\n\n"
                    encrypted_private_error_response = encrypt_with_public_key(private_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_private_error_response)
                    continue

                # command_parts[1] contains private information: specified recipient and message
                private_parts = command_parts[1].split(" ", 1)

                if len(private_parts) != 2:
                    private_error_response = "500\n\n"
                    encrypted_private_error_response = encrypt_with_public_key(private_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_private_error_response)
                    continue

                # Extract the recipient's username and the message.
                target_recipient = private_parts[0].strip()
                message = private_parts[1].strip()

                print(f"Private message from {client_username} to {target_recipient}")

                if target_recipient == "" or message == "":
                    private_error_response = "500\n\n"
                    encrypted_private_error_response = encrypt_with_public_key(private_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_private_error_response)
                    continue

                # Safely look for the recipient in active_clients.
                with active_clients_lock:
                    target_client = active_clients.get(target_recipient)

                # ------------------------------------------------
                # IF RECIPIENT DOES NOT EXIST
                # ------------------------------------------------
                if target_client is None:
                    # Notify the sender that the private command failed.
                    private_response = "500\n\n"
                    encrypted_private_response = encrypt_with_public_key(private_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_private_response)

                # ------------------------------------------------
                # RECIPIENT EXISTS
                # ------------------------------------------------
                else:
                    # Get the data socket of the recipient.
                    target_data_socket = target_client["data_socket"]

                    # Construct the private message for the recipient.
                    private_message = f"200\n\nPrivate\n{client_username}\n{message}"

                    encrypted_private_message = encrypt_with_public_key(private_message.encode(), target_client["public_key"]) 

                    # Send the private message only to the recipient.
                    target_data_socket.sendall(encrypted_private_message)

                    # Confirm success to the sender.
                    sender_response = "200\n\n"
                    encrypted_sender_response = encrypt_with_public_key(sender_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_sender_response)

            # ----------------
            # STOR COMMAND
            # ----------------
            elif command == "stor":
                if len(command_parts) != 2:
                    stor_error_response = "500\n\n"
                    encrypted_stor_error_response = encrypt_with_public_key(stor_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_stor_error_response)
                    continue

                filename = command_parts[1].strip()

                if filename == "":
                    stor_error_response = "500\n\n"
                    encrypted_stor_error_response = encrypt_with_public_key(stor_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_stor_error_response)
                    continue

                print(f"Stor {filename} requested by {client_username}")

                try:
                
                    # Receive the file contents through the DATA connection.
                    file_data = data_connection_socket.recv(4096)

                    file_data = decrypt_with_private_key(file_data, server_private_key)
    
                    # Create the file in the current directory and write the received bytes (data) to it.
                    # open() means open or create the requested file using the name stored in filename.
                    # "wb" means write binary mode, not to limit the range of file types.
                    # We use binary because file_data contains bytes.
                    with open(filename, "wb") as file:
                        file.write(file_data)
    
                    print("STOR complete")

                    stor_success_response = "200\n\n"
                    encrypted_stor_success_response = encrypt_with_public_key(stor_success_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_stor_success_response)
    
                except OSError:
                    stor_error_response = "500\n\n"
                    encrypted_stor_error_response = encrypt_with_public_key(stor_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_stor_error_response)

            # ----------------------------
            # RETR COMMAND
            # ----------------------------
            elif command == "retr":
                if len(command_parts) != 2:
                    retr_error_response = "500\n\n"
                    encrypted_retr_error_response = encrypt_with_public_key(retr_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_retr_error_response)
                    continue

                filename = command_parts[1].strip()

                if filename == "":
                    retr_error_response = "500\n\n"
                    encrypted_retr_error_response = encrypt_with_public_key(retr_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_retr_error_response)
                    continue
                    
                try:
                    # Open the requested file and read its contents as bytes.
                    with open(filename, "rb") as file:
                        file_data = file.read()
                        
                    print(f"Retr requested by {client_username}. Sending file: {filename}")

                    retr_response = b"200\n\n" + file_data
                    encrypted_retr_response = encrypt_with_public_key(retr_response, client_public_key)
                    data_connection_socket.sendall(encrypted_retr_response)

                    print("File sent.")

                except OSError:
                    retr_error_response = "500\n\n"
                    encrypted_retr_error_response = encrypt_with_public_key(retr_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_retr_error_response)

            # -------------
            # DELE COMMAND
            # ---------------
            elif command == "dele":
                if len(command_parts) != 2:
                    dele_error_response = "500\n\n"
                    encrypted_dele_error_response = encrypt_with_public_key(dele_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_dele_error_response)
                    continue

                filename = command_parts[1].strip()

                if filename == "":
                    dele_error_response = "500\n\n"
                    encrypted_dele_error_response = encrypt_with_public_key(dele_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_dele_error_response)
                    continue

                try:
                    print(f"Dele requested by {client_username}. Deleting file: {filename}")

                    # Delete the requested file from the server's current directory
                    os.remove(filename)
                    print("Delete complete")

                    dele_success_response = "200\n\n"
                    encrypted_dele_success_response = encrypt_with_public_key(dele_success_response.encode(), client_public_key)

                    # DELE only returns the status code on success.
                    data_connection_socket.sendall(encrypted_dele_success_response)

                except OSError:
                    dele_error_response = "500\n\n"
                    encrypted_dele_error_response = encrypt_with_public_key(dele_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_dele_error_response)
                        
            # -------------
            # LIST COMMAND
            # -------------

            elif command == "list":
                if len(command_parts) != 1:
                    list_error_response = "500\n\n"
                    encrypted_list_error_response = encrypt_with_public_key(list_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_list_error_response)
                    continue

                files = sorted(filename for filename in os.listdir(".")
                # (".") means current directory
                # Show every filename in the current directory
                # sorted() orders filenames in alphabetic order.
                               
                # Check whether the path used leads to an actual file
                # This avoids sending directories instead of files.
                if os.path.isfile(filename))

                # Convert the filenames into the required comma-separated format.
                files_list = ", ".join(files)
                
                print(f"List requested by {client_username}. Sending files.")

                # Send the status code and file list through the DATA connection.
                list_response = f"200\n\n{files_list}"
                encrypted_list_response = encrypt_with_public_key(list_response.encode(), client_public_key)
                data_connection_socket.sendall(encrypted_list_response)
            
            # ----------------------------------------------------
            # QUIT COMMAND
            # ----------------------------------------------------
            elif command == "quit":
                if len(command_parts) != 1:
                    quit_error_response = "500\n\n"
                    encrypted_quit_error_response = encrypt_with_public_key(quit_error_response.encode(), client_public_key)
                    data_connection_socket.sendall(encrypted_quit_error_response)
                    continue
                # Send success response to client
                print(f"Quit requested by {client_username}")
                quit_response = "200\n\n"
                encrypted_quit_response = encrypt_with_public_key(quit_response.encode(), client_public_key)
                data_connection_socket.sendall(encrypted_quit_response)
                return

            # For completely unknown commands.
            else:
                unknown_error_response = "500\n\n"
                encrypted_unknown_error_response = encrypt_with_public_key(unknown_error_response.encode(), client_public_key)
                data_connection_socket.sendall(encrypted_unknown_error_response)

    # ------------------------------------------------------------
    # UNEXPECTED CLIENT DISCONNECTION
    # ------------------------------------------------------------
    except OSError:
        print("Client disconnected unexpectedly:", client_control_address)

    # ------------------------------------------------------------
    # ALWAYS CLEAN UP THIS CLIENT
    # ------------------------------------------------------------
    finally:
    # If something failed before the data connection was accepted,
    # close the temporary data listening socket too.
        if data_listening_socket is not None:
            try:
                data_listening_socket.close()
            except OSError:
                pass

        # Remove client from active_clients if necessary,
        # broadcast logout, and close CONTROL/DATA sockets.
        cleanup_client(
            client_username,
            control_connection_socket,
            data_connection_socket
        )

# ------------------------------------------------------------
# ACCEPT CLIENTS CONTROL CONNECTION
# ------------------------------------------------------------

# MAIN THREAD: Keep accepting new CONTROL connections and give each
# one to a separate worker thread.
while True:
    # When the client successfully connects, accept() returns
    # a new connected TCP socket to communicate with this client.
    control_connection_socket, client_control_address = control_listening_socket.accept()
    # client_control_address contains information about the client (IP address
    # and random TCP Port assigned by OS)

    client_thread = threading.Thread( #Creates a Thread object
        # When threat started, run function with specified args.
        target=handle_client,
        args=(control_connection_socket, client_control_address)
    )

    client_thread.start()
