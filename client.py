import socket
import threading 
from cryptography.hazmat.primitives.asymmetric import rsa # imports rsa funcionality to create public/private key pair
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding # method for client to encrypt messages 
# RSA should not encrypt raw data. Needs padding to safely prepare the plaintext before RSA encryption.
from cryptography.hazmat.primitives import hashes # module for hash algorithm that OAEP (padding approach) will use.
RSA_PLAINTEXT_CHUNK_SIZE = 190 # maximum amount of plaintext to place into each RSA-OAEP encryption operation
RSA_CIPHERTEXT_BLOCK_SIZE = 256 # A 2048-bit RSA key produces an encrypted block of 2048 bits / 8 = 256 bytes
# so every plaintext chunk of up to 190 bytes will become 256 encrypted bytes (ciphertext).

def decrypt_with_private_key(encrypted_message, private_key):
    decrypted_message = b""
    for start in range(0, len(encrypted_message), RSA_CIPHERTEXT_BLOCK_SIZE):
        encrypted_block = encrypted_message[start:start + RSA_CIPHERTEXT_BLOCK_SIZE]
        decrypted_chunk = private_key.decrypt(encrypted_block, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        decrypted_message += decrypted_chunk
    return decrypted_message

def encrypt_with_public_key(message_bytes, public_key):
    encrypted_message = b""
    for start in range(0, len(message_bytes), RSA_PLAINTEXT_CHUNK_SIZE):
        chunk = message_bytes[start:start + RSA_PLAINTEXT_CHUNK_SIZE]
        encrypted_chunk = public_key.encrypt(chunk, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        encrypted_message += encrypted_chunk
    return encrypted_message

# getting server messages 
def receive_server_messages(data_connection_socket):

    global pending_command, pending_filename
    
    try:
        while True:
            response_bytes = data_connection_socket.recv(4096)

            if not response_bytes:
                break

            response_bytes = decrypt_with_private_key(response_bytes, client_private_key)
            print("Received encrypted message")

            # joining noti           
            if response_bytes.startswith(b"200\n\njoin\n"):
                continue

            # logging out noti
            if response_bytes.startswith(b"200\n\nlogout\n"):
                continue

            # broadcast noti
            if response_bytes.startswith(b"200\n\nBroadcast\n"):

                response = response_bytes.decode()

                response_parts = response.split("\n", 4)
                sender = response_parts[3]
                message = response_parts[4]

                print("200 status code received.")
                print(f"Broadcast message from {sender}: {message}")

                continue

            # private messaging
            if response_bytes.startswith(b"200\n\nPrivate\n"):

                response = response_bytes.decode()
                
                response_parts = response.split("\n", 4)
                sender = response_parts[3]
                message = response_parts[4]
                print("200 status code received.")
                print(f"{sender}: {message}")

                continue
            
            # who is it message
            if pending_command == "who":
                # Separate the status code from the returned usernames.
                status_code_bytes, response_data = response_bytes.split(b"\n\n", 1)
                status_code = status_code_bytes.decode().strip()

                if status_code == "200":
                    users_list = response_data.decode().strip()
                    print(f"200 status code received. Users currently connected: {users_list}")
                else:
                    print(f"{status_code} status code received.")

                pending_command = None
                pending_filename = None

                command_complete_event.set()
                continue

            # private messaging
            if pending_command == "private":

                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()
                
                if status_code == "200":
                    print("200 status code received. Message sent.")
                else:
                    print(f"{status_code} status code received.")

                pending_command = None
                pending_filename = None

                command_complete_event.set()
                continue
            
            # stor command
            if pending_command == "stor":

                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()

                if status_code == "200":
                    print("200 status code received. File Sent.")
                else:
                    print(f"{status_code} status code received.")

                pending_command = None
                pending_filename = None

                command_complete_event.set()

                continue

            # response list
            if pending_command == "list":
                status_code_bytes, response_data = response_bytes.split(b"\n\n", 1)
                status_code = status_code_bytes.decode().strip()

                if status_code == "200":
                    files_list = response_data.decode().strip()
                    print(f"200 status code received. Files: {files_list}")
                else:
                    print(f"{status_code} status code received.")

                pending_command = None
                pending_filename = None

                command_complete_event.set()
    
                continue

            # retr message
            if pending_command == "retr":

                status_code_bytes, file_data = response_bytes.split(b"\n\n", 1)
                status_code = status_code_bytes.decode().strip()

                if status_code == "200":
                    with open(pending_filename, "wb") as file:
                        file.write(file_data)

                    print("File retrieved.")

                else:
                    print(f"{status_code} status code received.")

                pending_command = None
                pending_filename = None
    
                command_complete_event.set()
    
                continue

            # dele message
            if pending_command == "dele":
            
                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()
            
                if status_code == "200":
                    print("200 status code received. File deleted.")
                else:
                    print(f"{status_code} status code received.")
            
                pending_command = None
                pending_filename = None
            
                command_complete_event.set()
            
                continue
            
            # quit commands
            if pending_command == "quit": 

                status_code = response_bytes.split(b"\n", 1)[0].decode().strip()

                print(f"{status_code} status code received.")

                pending_command = None
                pending_filename = None

                command_complete_event.set()

                continue

            
            # chat and server messages
            response = response_bytes.decode()
            print(response)

    except OSError:
        pass

print("Starting client...")
client_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) # stays only on the client - python cryptography object
client_public_key = client_private_key.public_key() # can be shared with the server - python cryptography object
# convert the public key into bytes
client_public_key_bytes = client_public_key.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)

control_connection_socket = None
data_connection_socket = None
receiver_thread = None

pending_command = None
pending_filename = None

command_complete_event = threading.Event()

try:
    # client control
    control_connection_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # connect command
    while True:
        user_input = input()
        command_parts = user_input.split()

        if len(command_parts) != 3:
            print("Invalid connect command.")
            continue

        command = command_parts[0]

        if command != "connect":
            print("Invalid connect command.")
            continue

        server_ip_address = command_parts[1]

        try:
            server_control_port = int(command_parts[2])
        except ValueError:
            print("Invalid port.")
            continue

        break

    # connection control
    control_connection_socket.connect((server_ip_address, server_control_port))

    # sending connect command
    control_connection_socket.sendall(user_input.encode())

    # getting connect command
    connect_response_bytes = control_connection_socket.recv(1024)

    if not connect_response_bytes:
        raise OSError("Server closed the control connection.")
    
    connect_response = connect_response_bytes.decode()

    status_code, response_data = connect_response.split("\n\n",1)
    # split content of response data only at first newline --> port number & server public key
    data_port_text, server_public_key_pem = response_data.split("\n", 1)
    # convert PEM text from server into the REAL RSA PUBLIC KEY from server
    server_public_key = serialization.load_pem_public_key(server_public_key_pem.encode())
    # client cannot encrypt with plain string so --> converts it back into bytes and then
    # reads those PEM bytes and reconstructs the actual RSA public key from the server. 

    if status_code != "200":
        print(f"{status_code} status code received.")
        raise ConnectionError("CONNECT request failed.")

    server_data_port = int(data_port_text.strip())
    print("200 status code received. Starting data connection on port")
    print(server_data_port)
    
    # client datas socket
    data_connection_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # data connection
    data_connection_socket.connect((server_ip_address, server_data_port))

    # login command
    while True:
        user_input = input()
        login_parts = user_input.split(" ", 1)

        if len(login_parts) != 2:
            print("Invalid login command.")
            continue

        command = login_parts[0]
        username = login_parts[1].strip()

        if command != "login" or username == "":
            print("Invalid login command.")
            continue

        # build the plaintext bytes login message
        login_message = b"login\n\n" + username.encode() + b"\n" + client_public_key_bytes
        encrypted_login = b"" # creates an empty bytes object to store RSA blocks
        # walks through the complete "login_message" in steps of chunk size specified below.
        for start in range(0, len(login_message), RSA_PLAINTEXT_CHUNK_SIZE):
            chunk = login_message[start:start + RSA_PLAINTEXT_CHUNK_SIZE] # stores every 190 bytes
            # takes the current plaintext chunk and encrypts using server public key
            encrypted_chunk = server_public_key.encrypt(chunk, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))            
            # OAEP padding prepares the data safely before RSA encryption, since RSA should not encrypt raw plaintext
            # Applying OAEP hash and mask generation function.   
            encrypted_login += encrypted_chunk # append the encrypted block to complete encrypted message
        
        control_connection_socket.sendall(encrypted_login)

        login_response_bytes = b""
        while len(login_response_bytes) < RSA_CIPHERTEXT_BLOCK_SIZE:
            response_piece = data_connection_socket.recv(RSA_CIPHERTEXT_BLOCK_SIZE - len(login_response_bytes))
            if not response_piece:
                raise OSError("Server closed the data connection.")
            login_response_bytes += response_piece

        if not login_response_bytes:
            raise OSError("Server closed the data connection.")
        
        login_response_plaintext = decrypt_with_private_key(login_response_bytes, client_private_key)
        print("Received encrypted message")

        login_response = login_response_plaintext.decode()

        status_code = login_response.split("\n", 1)[0].strip()

        if status_code == "200":
            print("200 status code received. Login successful")
            break

        print(f"{status_code} status code received.")

    # data reci
    receiver_thread = threading.Thread(
        target=receive_server_messages,
        args=(data_connection_socket,),
        daemon=True
    )

    receiver_thread.start()
    
    # user commands
    while True:
        user_input = input()

        command_parts = user_input.split(" ", 1)
        command = command_parts[0]

        # who command
        if command == "who":
            if len(command_parts) != 1:
                print("Invalid who command.")
                continue

            pending_command = "who"
            pending_filename = None

            command_complete_event.clear()

            encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)
            
            control_connection_socket.sendall(encrypted_command)

            command_complete_event.wait()
            continue

        # private commands
        if command == "private":
            if len(command_parts) != 2:
                print("Invalid private command.")
                continue

            private_parts = command_parts[1].split(" ", 1)

            if len(private_parts) != 2:
                print("Invalid private command.")
                continue

            target_username = private_parts[0].strip()
            message = private_parts[1].strip()

            if target_username == "" or message == "":
                print("Invalid private command.")
                continue

            pending_command = "private"
            pending_filename = None

            command_complete_event.clear()

            encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)
            
            control_connection_socket.sendall(encrypted_command)
            command_complete_event.wait()
            continue

        # stor command
        if command == "stor":
            if len(command_parts) != 2:
                print("Invalid stor command.")
                continue

            filename = command_parts[1].strip()
    
            if filename == "":
                print("Invalid stor command.")
                continue
    
            try:
                with open(filename, "rb") as file:
                    file_data = file.read()
    
            except OSError:
                print("File not found.")
                continue

            pending_command = "stor"
            pending_filename = filename
    
            command_complete_event.clear()

            encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)
            
            control_connection_socket.sendall(encrypted_command)

            encrypted_file_data = encrypt_with_public_key(file_data, server_public_key)
    
            data_connection_socket.sendall(encrypted_file_data)
    
            command_complete_event.wait()
    
            continue

        # list commands
        if command == "list":
            if len(command_parts) != 1:
                print("Invalid list command.")
                continue

            pending_command = "list"
            pending_filename = None
            command_complete_event.clear()

            encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)
            
            control_connection_socket.sendall(encrypted_command)
            command_complete_event.wait()

            continue
        # retr commands
        if command == "retr":
            if len(command_parts) != 2:
                print("Invalid retr command.")
                continue

            filename = command_parts[1].strip()

            if filename == "":
                print("Invalid retr command.")
                continue

            pending_command = "retr"
            pending_filename = filename
            command_complete_event.clear()

            encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)
            
            control_connection_socket.sendall(encrypted_command)
    
            command_complete_event.wait()
    
            continue

        # dele command
        if command == "dele":
            if len(command_parts) != 2:
                print("Invalid dele command.")
                continue
            
            filename = command_parts[1].strip()
            
            if filename == "":
                print("Invalid dele command.")
                continue
            
            pending_command = "dele"
            pending_filename = filename
            
            command_complete_event.clear()

            encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)
        
            control_connection_socket.sendall(encrypted_command)
        
            command_complete_event.wait()
        
            continue

        # quit command
        if command == "quit":
            if len(command_parts) != 1:
                print("Invalid quit command.")
                continue

            pending_command = "quit"
            pending_filename = None

            command_complete_event.clear()

            encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)

            control_connection_socket.sendall(encrypted_command)

            command_complete_event.wait()

            break
        
        # other command
        encrypted_command = encrypt_with_public_key(user_input.encode(), server_public_key)
        control_connection_socket.sendall(encrypted_command)

    # final server response
    receiver_thread.join()

except KeyboardInterrupt:
    pass
except OSError:
    pass

# clean up
finally:
    if control_connection_socket is not None:
        try:
            control_connection_socket.close()
        except OSError:
            pass

    if data_connection_socket is not None:
        try:
            data_connection_socket.close()
        except OSError:
            pass
