"""合成数据质量检查；不读取真实账号，合并等待与本地处理时间分开。"""
import argparse
import asyncio
import json
import math
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from unittest.mock import AsyncMock, patch

from engine.service import Service
from engine.store import Store


def p95(values): return sorted(values)[math.ceil(len(values) * .95) - 1]


async def reminder_quality(folder):
    service = Service(folder, demo=True); service.account = 1; service.auth = 'ready'
    service.emit = AsyncMock(); service.publish = AsyncMock()
    values = []
    try:
        with patch('engine.service.asyncio.sleep', new=AsyncMock()):
            for index in range(1000):
                service.notice_counts[42] = 1; service.notice_message_ids[42] = index
                begin = time.perf_counter(); await service.delayed_notice(42, '合成测试', '合成消息')
                values.append(time.perf_counter() - begin)
        assert service.emit.await_count == 1000
        return {'events': 1000, 'p95_ms': p95(values) * 1000, 'merge_window_seconds': 10,
                'max_ms': max(values) * 1000,
                'scope': '合并窗口结束至引擎提醒事件提交；不含原生系统通知 API、网络和显示时间'}
    finally: service.store.close()


def startup_quality(executable):
    values = []
    for _ in range(20):
        with tempfile.TemporaryDirectory(prefix='tga-start-') as folder:
            address = str(Path(folder) / 'e.sock'); begin = time.perf_counter()
            process = subprocess.Popen([executable, '--root', folder, '--socket', address, '--demo'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(5)
                    while True:
                        try: client.connect(address); break
                        except (FileNotFoundError, ConnectionRefusedError):
                            if process.poll() is not None or time.perf_counter() - begin > 10: raise RuntimeError('引擎启动失败')
                            time.sleep(.01)
                    file = client.makefile('rwb'); state = json.loads(file.readline())
                    assert state['data']['auth'] == 'ready'
                    values.append(time.perf_counter() - begin)
                    file.write(b'{"protocol":1,"id":"stop","method":"shutdown"}\n'); file.flush()
                    process.wait(timeout=10)
            finally:
                if process.poll() is None: process.terminate(); process.wait(timeout=10)
    return {'runs': 20, 'p95_seconds': p95(values), 'scope': '打包引擎就绪，界面启动目标需另行观察'}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--executable', required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tga-quality-') as folder:
        reminder = asyncio.run(reminder_quality(folder))
    with tempfile.TemporaryDirectory(prefix='tga-search-') as folder:
        store = Store(folder); store.put_conversation(1, 42, '合成会话', 'private')
        store.put_messages(1, 42, [{'id': index, 'date': 1700000000 + index, 'sender': '测试成员', 'text': f'中文会议记录 English project {index}'} for index in range(50000)])
        values = []
        for index in range(100):
            filters = {'query': ['中文', 'English', '49999', '不存在'][index % 4]}
            if index % 2: filters.update(chat=42, sender='测试成员', start=1700000000, end=1700050000)
            begin = time.perf_counter(); store.search(1, **filters); values.append(time.perf_counter() - begin)
        diagnostics = Service(folder, demo=True)
        diagnostics.account = 1
        raw = json.dumps(diagnostics.diagnostics())
        assert not any(word in raw for word in ('会议记录', '测试成员', 'English', 'api_hash', 'phone', 'keyword', 'identity'))
        diagnostics.store.close(); store.close()
    report = {'engine_startup': startup_quality(args.executable), 'reminder': reminder,
              'search': {'rows': 50000, 'runs': 100, 'p95_ms': p95(values) * 1000}, 'diagnostic_sentinel_check': 'passed'}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)); print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__': main()
