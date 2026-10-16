"""版本化流式认证加密；业务ZIP和明文临时文件只存在于受限临时目录。"""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b'JIXUNB1\n'
HEADER_SIZE = len(MAGIC) + 16 + 12
MAX_SIZE = 16 * 1024**3


def is_encrypted(path):
    with Path(path).open('rb') as source: return source.read(len(MAGIC)) == MAGIC


def key(password, salt, creating=False):
    if not isinstance(password, str) or not (10 if creating else 1) <= len(password) <= 1024:
        raise ValueError('备份密码创建时至少10个字符，最多1024个字符；密码不会保存，遗失无法恢复')
    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode('utf-8'))


def encrypt(source, destination, password):
    source, destination = Path(source), Path(destination)
    if source.stat().st_size > MAX_SIZE: raise ValueError('单个加密备份最多16GB')
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    encryptor = Cipher(algorithms.AES(key(password, salt, creating=True)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    fd, temporary = tempfile.mkstemp(prefix='.jixun-encrypted-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output, source.open('rb') as plain:
            output.write(header)
            for chunk in iter(lambda: plain.read(1024*1024), b''): output.write(encryptor.update(chunk))
            output.write(encryptor.finalize()); output.write(encryptor.tag)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally: Path(temporary).unlink(missing_ok=True)


def decrypt(source, destination, password):
    source, destination = Path(source), Path(destination)
    size = source.stat().st_size
    if not HEADER_SIZE + 16 < size <= MAX_SIZE + HEADER_SIZE + 16:
        raise ValueError('加密备份长度无效或超过16GB')
    if size + 50*1024*1024 > shutil.disk_usage(destination.parent).free: raise ValueError('临时空间不足，尚未解密或修改资料')
    created = False
    digest = hashlib.sha256()
    try:
        with source.open('rb') as encrypted:
            header = encrypted.read(HEADER_SIZE)
            if not header.startswith(MAGIC): raise ValueError('不支持的加密备份版本')
            digest.update(header)
            salt, nonce = header[len(MAGIC):len(MAGIC)+16], header[-12:]
            encrypted.seek(-16, os.SEEK_END); tag = encrypted.read(16); encrypted.seek(HEADER_SIZE)
            decryptor = Cipher(algorithms.AES(key(password, salt)), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            remaining = size - HEADER_SIZE - 16
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, 'wb') as output:
                while remaining:
                    chunk = encrypted.read(min(1024*1024, remaining))
                    if not chunk: raise ValueError('加密备份不完整')
                    digest.update(chunk)
                    output.write(decryptor.update(chunk)); remaining -= len(chunk)
                output.write(decryptor.finalize()); digest.update(tag)
    except InvalidTag:
        if created: destination.unlink(missing_ok=True)
        raise ValueError('密码错误或备份已损坏，现有资料未更改') from None
    except Exception:
        if created: destination.unlink(missing_ok=True)
        raise
    return digest.hexdigest()
