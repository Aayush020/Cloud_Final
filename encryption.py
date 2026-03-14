import os
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding

AES_KEY = b'CloudDeDupeKey32'

def pad(data):
    padder = padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()

def unpad(data):
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(data) + unpadder.finalize()

def encrypt_file(file_bytes):
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(pad(file_bytes)) + encryptor.finalize()
    return iv + encrypted

def decrypt_file(encrypted_bytes):
    iv = encrypted_bytes[:16]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    return unpad(decryptor.update(encrypted_bytes[16:]) + decryptor.finalize())

def encrypt_metadata(text):
    return base64.b64encode(encrypt_file(text.encode('utf-8'))).decode('utf-8')

def decrypt_metadata(encrypted_b64):
    return decrypt_file(base64.b64decode(encrypted_b64.encode('utf-8'))).decode('utf-8')
