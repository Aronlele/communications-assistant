"""仅供隔离验收使用：确认连接成功后才允许向打包引擎发送请求。"""
import socket
import time


def connect_engine(address, process, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError(f'消息引擎在连接前已退出（状态 {process.returncode}）')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('消息引擎启动等待超时，尚未建立本地连接；未发送探测请求')
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(min(remaining, .2))
        try:
            client.connect(str(address))
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError):
            client.close()
            time.sleep(min(.05, remaining))
        except BaseException:
            client.close()
            raise
        else:
            return client
