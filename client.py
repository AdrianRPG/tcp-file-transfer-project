import socket # Python's built-in library for socket/network programming.
import threading # Python's library that lets multiple sections of program
# execute concurrently.

# ------------------------------------------------------------
# RECEIVE SERVER MESSAGES
# ------------------------------------------------------------
def receive_server_messages(data_connection_socket):

    global pending_command, pending_filename
    
    try:
        while True:
            # Wait for a response/message from the server.
            response_bytes = data_connection_socket.recv(1024)

            # recv() returning b"" means the server closed the data connection.
            if not response_bytes:
                break

            # ------------------------------------------------
            # JOIN NOTIFICATION
            # ------------------------------------------------            
            if response_bytes.startswith(b"200\n\njoin\n"):
                # JOIN notifications are required by the server protocol,
                # but the client output example does not display them.
                continue

            # ------------------------------------------------
            # LOGOUT NOTIFICATION
            # ------------------------------------------------
            if response_bytes.startswith(b"200\n\nlogout\n"):
                # LOGOUT notifications are required by the server protocol,
                # but the client output example does not display them.
                continue

            # ------------------------------------------------
            # BROADCAST MESSAGE
            # ------------------------------------------------
            if response_bytes.startswith(b"200\n\nBroadcast\n"):

                response = response_bytes.decode()

                response_parts = response.split("\n", 4)
                sender = response_parts[3]
                message = response_parts[4]

                print("200 status code received.")
                print(f"Broadcast message from {sender}: {message}")

                continue

            # ------------------------------------------------
            # PRIVATE MESSAGE
            # ------------------------------------------------
            if response_bytes.startswith(b"200\n\nPrivate\n"):

                # Convert the complete private message into text.
                response = response_bytes.decode()
                
                response_parts = response.split("\n", 4)
                sender = response_parts[3]
                message = response_parts[4]
                print("200 status code received.")
                print(f"{sender}: {message}")

                continue
            
            # ------------------------------------------------
            # WHO RESPONSE
            # ------------------------------------------------
            if pending_command == "who":
                # Separate the status code from the returned usernames.
                status_code_bytes, response_data = response_bytes.split(b"\n\n", 1)
                status_code = status_code_bytes.decode().strip()

                if status_code == "200":
                    users_list = response_data.decode().strip()
                    print(f"200 status code received. Users currently connected: {users_list}")
                else:
                    print(f"{status_code} status code received.")

                # WHO has finished.
                pending_command = None
                pending_filename = None

                # Tell the main thread that the command is complete.
                command_complete_event.set()
                continue

            # ------------------------------------------------
            # PRIVATE SENDER RESPONSE
            # ------------------------------------------------
            if pending_command == "private":

                # The sender receives only the status code.
                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()
                
                if status_code == "200":
                    print("200 status code received. Message sent.")
                else:
                    print(f"{status_code} status code received.")

                # PRIVATE has finished.
                pending_command = None
                pending_filename = None

                # Tell the main thread that the command is complete.
                command_complete_event.set()
                continue
            
            # ------------------------------------------------
            # STOR RESPONSE
            # ------------------------------------------------
            if pending_command == "stor":

                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()

                if status_code == "200":
                    print("200 status code received. File Sent.")
                else:
                    print(f"{status_code} status code received.")

                # STOR has finished.
                pending_command = None
                pending_filename = None

                # Tell the main thread that the command is complete.
                command_complete_event.set()

                continue

            # ------------------------------------------------
            # LIST RESPONSE
            # ------------------------------------------------
            if pending_command == "list":
                # Separate the status code from the returned file list.
                status_code_bytes, response_data = response_bytes.split(b"\n\n", 1)
                status_code = status_code_bytes.decode().strip()

                if status_code == "200":
                    files_list = response_data.decode().strip()
                    print(f"200 status code received. Files: {files_list}")
                else:
                    print(f"{status_code} status code received.")

                # LIST has finished.
                pending_command = None
                pending_filename = None

                # Tell the main thread that the command is complete.
                command_complete_event.set()
    
                continue

            # ------------------------------------------------
            # RETR RESPONSE
            # ------------------------------------------------
            if pending_command == "retr":

                # Separate the status code from the returned file contents.
                status_code_bytes, file_data = response_bytes.split(b"\n\n", 1)
                status_code = status_code_bytes.decode().strip()

                if status_code == "200":
                    # Create/overwrite the local file using the bytes received from the server.
                    with open(pending_filename, "wb") as file:
                        file.write(file_data)

                    print("File retrieved.")

                else:
                    print(f"{status_code} status code received.")

                # RETR has finished.
                pending_command = None
                pending_filename = None
    
                # Tell the main thread that RETR is complete.
                command_complete_event.set()
    
                continue

            # ------------------------------------------------
            # DELE RESPONSE
            # ------------------------------------------------
            if pending_command == "dele":
            
                # The status code is the first line of the response.
                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()
            
                if status_code == "200":
                    print("200 status code received. File deleted.")
                else:
                    print(f"{status_code} status code received.")
            
                # DELE has finished.
                pending_command = None
                pending_filename = None
            
                # Tell the main thread that the command is complete.
                command_complete_event.set()
            
                continue
            
            # ------------------------------------------------
            # QUIT RESPONSE
            # ------------------------------------------------
            if pending_command == "quit": 

                # The status code is the first line of the response.
                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()

                print(f"{status_code} status code received.")

                # QUIT has finished.
                pending_command = None
                pending_filename = None

                # Tell the main thread that the response was processed.
                command_complete_event.set()

                continue

            
            # ------------------------------------------------
            # NORMAL CHAT/SERVER MESSAGE
            # ------------------------------------------------
            # Convert the received bytes into a string.
            response = response_bytes.decode()
            print(response)

    # The data connection may be closed while this thread
    # is waiting inside recv().
    except OSError:
        pass

print("Starting client...")

# Variables initialized as None so they can safely
# be checked and closed later if something unexpected happens.
control_connection_socket = None
data_connection_socket = None
receiver_thread = None

# Tracks which file-transfer command is currently waiting for a DATA response
pending_command = None
pending_filename = None

# Allows the receiver thread to signal when the current command is complete
command_complete_event = threading.Event()

try:
    # ------------------------------------------------------------
    # CREATE THE CLIENT'S CONTROL SOCKET
    # ------------------------------------------------------------
    # Ask the OS to create a TCP socket (networking endpoint).
    # The client will eventually use this control connection to send commands
    # such as: login, who, broadcast, private, and quit
    control_connection_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # At this point, the control socket does not yet have the
    # client's IP address and a TCP port assigned by the OS.

    # ------------------------------------------------------------
    # RECEIVE AND PARSE THE CONNECT COMMAND
    # ------------------------------------------------------------
    while True:
        # The user enters a command typed (strings).
        user_input = input()
        command_parts = user_input.split()

        # Expected format: connect (ip address) (control_port)
        if len(command_parts) != 3:
            print("Invalid connect command.")
            continue

        # Separate the command into individual arguments.
        command = command_parts[0]

        if command != "connect":
            print("Invalid connect command.")
            continue

        server_ip_address = command_parts[1]

        try:
            # Command-line/user input is received as strings,
            # so the TCP port must be converted into an integer.
            server_control_port = int(command_parts[2])
        except ValueError:
            print("Invalid port.")
            continue

        # Valid connect command
        break

    # ------------------------------------------------------------
    # ESTABLISH THE CONTROL CONNECTION
    # ------------------------------------------------------------
    # Connect the client's control socket to server's control  socket
    control_connection_socket.connect((server_ip_address, server_control_port))
    # The client's OS automatically selects the available
    # ephemeral (random) source TCP port for the connection.

    # ------------------------------------------------------------
    # SEND THE CONNECT COMMAND
    # ------------------------------------------------------------
    # Send the validated CONNECT command through the CONTROL connection.
    control_connection_socket.sendall(user_input.encode())

    # ------------------------------------------------------------
    # RECEIVE THE CONNECT RESPONSE
    # ------------------------------------------------------------
    # The server responds through the already-established
    # control TCP connection.
    connect_response_bytes = control_connection_socket.recv(1024)

    if not connect_response_bytes:
        raise OSError("Server closed the control connection.")
    
    # Convert the received bytes into a Python string.
    connect_response = connect_response_bytes.decode()

    # ------------------------------------------------------------
    # EXTRACT THE STATUS CODE & SERVER'S DATA PORT
    # ------------------------------------------------------------
    # The response format is:
    # Status Code
    #
    # Data Port
    # Split the string using "\n\n" as the separator but only once.
    status_code, response_data = connect_response.split("\n\n",1)

    # CONNECT must succeed before the DATA connection can be created.
    if status_code != "200":
        print(f"{status_code} status code received.")
        raise ConnectionError("CONNECT request failed.")

    # Convert the DATA port from string to integer.
    server_data_port = int(response_data.strip())

    # Display the successful CONNECT output
    print("200 status code received. Starting data connection on port")
    print(server_data_port)
    
    # ------------------------------------------------------------
    # CREATE THE CLIENT'S DATA SOCKET
    # ------------------------------------------------------------
    # Ask the OS to create a new IPv4 TCP socket for data connection.
    # It will be used to receive server responses, broadcasts, private messages, etc.
    data_connection_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # ------------------------------------------------------------
    # ESTABLISH THE DATA CONNECTION
    # ------------------------------------------------------------
    # Connect the client's data socket to the data port
    # that the server provided in the connect response.
    data_connection_socket.connect((server_ip_address, server_data_port))

    # ------------------------------------------------------------
    # RECEIVE AND PROCESS LOGIN COMMAND
    # ------------------------------------------------------------
    while True:
        # Wait for the user to enter the LOGIN command.
        user_input = input()

        # Expected format: login <username>
        login_parts = user_input.split(" ", 1)

        if len(login_parts) != 2:
            print("Invalid login command.")
            continue

        command = login_parts[0]
        username = login_parts[1].strip()

        if command != "login" or username == "":
            print("Invalid login command.")
            continue

        # Send the LOGIN command through the CONTROL connection.
        control_connection_socket.sendall(user_input.encode())

        # Receive the LOGIN response through the DATA connection.
        login_response_bytes = data_connection_socket.recv(1024)

        if not login_response_bytes:
            raise OSError("Server closed the data connection.")

        login_response = login_response_bytes.decode()

        # The status code is the first line of the response.
        status_code = login_response.split("\n", 1)[0].strip()

        if status_code == "200":
            print("200 status code received. Login successful")
            break

        # LOGIN failed, so allow the user to try again.
        print(f"{status_code} status code received.")

    # ------------------------------------------------------------
    # START THE DATA RECEIVER THREAD
    # ------------------------------------------------------------
    # LOGIN has already completed, so the receiver thread can now
    # listen for later server messages on the DATA connection.
    receiver_thread = threading.Thread(
        target=receive_server_messages,
        args=(data_connection_socket,),
        daemon=True
    )

    receiver_thread.start()
    
    # ------------------------------------------------------------
    # SEND USER COMMANDS
    # ------------------------------------------------------------
    while True:
        # Wait for the user to enter a command.
        user_input = input()

        # Split the command only once.
        command_parts = user_input.split(" ", 1)
        command = command_parts[0]

        # --------------------------------------------------------
        # WHO COMMAND
        # --------------------------------------------------------
        if command == "who":
            if len(command_parts) != 1:
                print("Invalid who command.")
                continue

            # Tell the receiver thread that the next response belongs to WHO.
            pending_command = "who"
            pending_filename = None

            # Reset the completion signal before sending the command.
            command_complete_event.clear()

            # Send WHO through the CONTROL connection.
            control_connection_socket.sendall(user_input.encode())

            # Wait until the receiver thread processes the response.
            command_complete_event.wait()
            continue

        # --------------------------------------------------------
        # PRIVATE COMMAND
        # --------------------------------------------------------
        if command == "private":
            if len(command_parts) != 2:
                print("Invalid private command.")
                continue

            # Expected format: private <username> <message>
            private_parts = command_parts[1].split(" ", 1)

            if len(private_parts) != 2:
                print("Invalid private command.")
                continue

            target_username = private_parts[0].strip()
            message = private_parts[1].strip()

            if target_username == "" or message == "":
                print("Invalid private command.")
                continue

            # Tell the receiver thread that the next status response belongs to PRIVATE.
            pending_command = "private"
            pending_filename = None

            # Reset the completion signal before sending the command.
            command_complete_event.clear()

            # Send PRIVATE through the CONTROL connection.
            control_connection_socket.sendall(user_input.encode())

            # Wait until the receiver thread processes the sender response.
            command_complete_event.wait()
            continue

        # --------------------------------------------------------
        # STOR COMMAND
        # --------------------------------------------------------
        if command == "stor":
            if len(command_parts) != 2:
                print("Invalid stor command.")
                continue

            filename = command_parts[1].strip()
    
            if filename == "":
                print("Invalid stor command.")
                continue
    
            try:
                # Open the file to read the contents as bytes.
                # "rb" means read binary.
                with open(filename, "rb") as file:
                    file_data = file.read()
    
            except OSError:
                print("File not found.")
                continue

            # Tell the receiver thread that the next response belongs to STOR.
            pending_command = "stor"
            pending_filename = filename
    
            # Make sure the completion signal starts in the unset state.
            command_complete_event.clear()
            
            # Send the STOR command through CONTROL socket
            control_connection_socket.sendall(user_input.encode())
    
            # Send contents or data of the file through DATA socket
            data_connection_socket.sendall(file_data)
    
            # Wait until the receiver thread processes the server response.
            command_complete_event.wait()
    
            continue

        # --------------------------------------------------------
        # LIST COMMAND
        # --------------------------------------------------------
        if command == "list":
            if len(command_parts) != 1:
                print("Invalid list command.")
                continue

            # Tell the receiver thread that the next response belongs to LIST command.
            pending_command = "list"
            pending_filename = None

            # Reset the completion signal before sending the command.
            command_complete_event.clear()
    
            # Send LIST command through the CONTROL connection.
            control_connection_socket.sendall(user_input.encode())
    
            # Wait until the receiver thread processes the response.
            command_complete_event.wait()

            continue

        # --------------------------------------------------------
        # RETR COMMAND
        # --------------------------------------------------------
        if command == "retr":
            if len(command_parts) != 2:
                print("Invalid retr command.")
                continue

            filename = command_parts[1].strip()

            if filename == "":
                print("Invalid retr command.")
                continue

            # Tell the receiver thread that the next response belongs to RETR
            pending_command = "retr"
            pending_filename = filename

            # Reset the completion signal before sending the command.
            command_complete_event.clear()
    
            # Send RETR through the CONTROL connection.
            control_connection_socket.sendall(user_input.encode())
    
            # Wait until the receiver thread processes and saves the files.
            command_complete_event.wait()
    
            continue

        # --------------------------------------------------------
        # DELE COMMAND
        # --------------------------------------------------------
        if command == "dele":
            if len(command_parts) != 2:
                print("Invalid dele command.")
                continue
            
            filename = command_parts[1].strip()
            
            if filename == "":
                print("Invalid dele command.")
                continue
            
            # Tell the receiver thread that the next response belongs to DELE.
            pending_command = "dele"
            pending_filename = filename
            
            # Reset the completion signal before sending the command.
            command_complete_event.clear()
        
            # Send DELE through the CONTROL connection.
            control_connection_socket.sendall(user_input.encode())
        
            # Wait until the receiver thread processes the server response.
            command_complete_event.wait()
        
            continue

        # --------------------------------------------------------
        # QUIT COMMAND
        # --------------------------------------------------------
        if command == "quit":
            if len(command_parts) != 1:
                print("Invalid quit command.")
                continue

            # Tell the receiver thread that the next response belongs to QUIT.
            pending_command = "quit"
            pending_filename = None

            # Reset the completion signal before sending the command.
            command_complete_event.clear()

            # Send QUIT through the CONTROL connection.
            control_connection_socket.sendall(user_input.encode())

            # Wait until the receiver thread processes the server response.
            command_complete_event.wait()

            break
        
        # --------------------------------------------------------
        # ALL OTHER COMMANDS
        # --------------------------------------------------------
        # Send entire command line through the control connection.
        control_connection_socket.sendall(user_input.encode())

    # ------------------------------------------------------------
    # WAIT FOR THE FINAL SERVER RESPONSE BEFORE ACTUALLY QUITTING
    # ------------------------------------------------------------
    # Wait until the data receiver thread finishes.
    # The server will send the quit response and then close the data connection.
    receiver_thread.join()

except KeyboardInterrupt:
    pass
except OSError:
    pass

# ------------------------------------------------------------
# CLIENT CLEANUP
# ------------------------------------------------------------
finally:
    # Close the client's control socket if it was created.
    if control_connection_socket is not None:
        try:
            control_connection_socket.close()
        except OSError:
            pass

    # Close the client's data socket if it was created.
    if data_connection_socket is not None:
        try:
            data_connection_socket.close()
        except OSError:
            pass
