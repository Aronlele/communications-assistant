"""检查实际打包引擎，所有数据和演示账号均在临时目录中。"""
import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tga-smoke-") as folder:
        root = Path(folder) / "data"
        address = str(Path(folder) / "engine.sock")
        process = subprocess.Popen([args.executable, "--root", str(root), "--socket", address, "--demo"],
                                   env={"PATH": "/usr/bin:/bin"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            client = socket.socket(socket.AF_UNIX)
            for _ in range(100):
                try:
                    client.connect(address)
                    break
                except FileNotFoundError:
                    time.sleep(0.05)
            client.settimeout(10)
            file = client.makefile("rwb")
            sequence = 0
            def command(method, values=None):
                nonlocal sequence
                sequence += 1
                identifier = str(sequence)
                file.write((json.dumps({"protocol": 1, "id": identifier, "method": method, "args": values or {}}) + "\n").encode())
                file.flush()
                while True:
                    item = json.loads(file.readline())
                    if item.get("id") == identifier:
                        assert "error" not in item, item
                        return item["result"]
            state = command("state")
            assert state["demo"] and state["account"] == 999001
            assert len(state['accounts']) == 2
            second_slot = state['accounts'][1]['slot']
            assert os.stat(address).st_mode & 0o777 == 0o600
            assert os.stat(root / "assistant.sqlite").st_mode & 0o777 == 0o600
            result = command("search", {"query": "项目"})
            assert result["count"] == 3
            command("control", {"action": "pause_sends"})
            command("control", {"action": "pause_all"})
            state = command("control", {"action": "resume_all"})
            assert state["settings"]["pause_sends"] and not state["settings"]["pause_all"]
            prompt = "查岗提醒（编号 12）\n请在 5 分钟内只回复下面的关键词：\n完成\n回复时限说明："
            preview = command("preview", {"text": prompt})
            assert preview["keyword"] == "完成" and preview["recognized"]
            output = Path(folder) / "export.json"
            result = command("export", {"path": str(output), "query": "项目"})
            assert result["count"] == 3 and output.exists()
            diagnostics = command("diagnostics")
            assert "identity" not in diagnostics and "keyword" not in diagnostics
            backup = Path(folder) / 'sample.tgabackup'
            counts = command('backup', {'path': str(backup)})
            assert counts['messages'] == 9
            preview = command('backup_preview', {'path': str(backup)})
            assert preview['counts']['messages'] == 9
            command('restore_backup', {'path': str(backup), 'confirmed_account': 999001, 'digest': preview['digest']})
            state = command('state')
            assert not state['settings']['rule']['automatic'] and not state['settings']['online']
            assert command('search')['count'] == 9
            command('select_account', {'slot': second_slot})
            state = command('state')
            assert state['account'] == 999002 and state['slot'] == second_slot
            assert state['data_directory'] == 'accounts/' + second_slot
            assert command('search')['count'] == 9
            command('control', {'action': 'pause_all'})
            command('select_account', {'slot': 'legacy'})
            assert not command('state')['settings']['pause_all']
            template = {'mode': 'fixed', 'match_text': '在线抽查，请回复任意内容',
                        'match_mode': 'exact', 'reply_text': '1', 'valid_minutes': 5}
            command('reply_template', template)
            preview = command('preview', {'text': template['match_text']})
            assert preview['recognized'] and preview['keyword'] == '1' and preview['reply_mode'] == 'fixed'
            assert not command('preview', {'text': '普通消息'})['recognized']
            command('select_account', {'slot': second_slot})
            assert command('state')['settings']['rule']['mode'] == 'keyword'
            command('select_account', {'slot': 'legacy'})
            assert command('state')['settings']['rule']['mode'] == 'fixed'
            command("shutdown")
            process.wait(timeout=10)
            assert process.returncode == 0
            assert not Path(address).exists()
            print("Packaged engine: IPC, demo isolation, search, preview, pause, export, permissions and shutdown passed")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
