"""检查实际打包引擎，所有数据和演示账号均在临时目录中。"""
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from engine_probe import connect_engine


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
            client = connect_engine(address, process)
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
            first = command('task_save', {'id': 'new', 'name': '关键词演示任务', 'target': '1003',
                                           'mode': 'keyword', 'sample_text': prompt})
            first_id = first['saved_task_id']
            preview = command('task_preview', {'id': first_id, 'text': prompt})
            assert preview['recognized'] and preview['number'] == '12' and preview['keyword'] == '完成'
            second = command('task_save', {'id': 'new', 'name': '固定回复演示任务', 'target': '1002',
                                            **template, 'sample_text': template['match_text']})
            second_id = second['saved_task_id']
            assert first_id != second_id and len(second['tasks']) == 2
            assert not any(rule['automatic'] for rule in second['tasks'])
            assert next(row for row in second['accounts'] if row['slot'] == 'legacy')['task_count'] == 2
            assert command('task_results', {'status': 'attention'})['count'] == 0
            command('setup_complete')
            command('select_account', {'slot': second_slot})
            assert len(command('state')['tasks']) == 0
            command('select_account', {'slot': 'legacy'})
            state = command('state')
            assert state['settings']['setup_completed']
            assert next(rule for rule in state['tasks'] if rule['id'] == first_id)['sample_text'] == prompt
            assert len(command('task_templates')['rows']) == 3
            copied = command('task_duplicate', {'id': second_id})['saved_task_id']
            assert not next(rule for rule in command('state')['tasks'] if rule['id'] == copied)['automatic']
            portable = command('task_template_export', {'id': second_id})
            assert 'target' not in portable['rule'] and not command('task_template_import', {'document': portable})['automatic']
            cases = [{'text': template['match_text'], 'should_match': True, 'expected_reply': '1'}, {'text': '普通通知', 'should_match': False}]
            command('task_save', dict(command('state')['tasks'][1], id=second_id, target='1002', sample_cases=cases))
            assert command('task_cases_preview', {'id': second_id})['passed']
            preview = command('archive_cleanup_preview', {'scope': 'selected', 'messages': [{'chat': 1001, 'id': 1}]})
            assert preview['count'] == 1
            command('archive_cleanup_apply', {'token': preview['token'], 'confirmed_account': 999001})
            assert command('search', {'chat': 1001})['count'] == 2
            assert command('issues')['rows'] is not None
            command('select_account', {'slot': second_slot})
            assert command('search', {'chat': 1001})['count'] == 3
            command('select_account', {'slot': 'legacy'})
            command('task_delete', {'id': copied, 'confirmed_id': copied})
            command('task_delete', {'id': second_id, 'confirmed_id': second_id})
            command('task_delete', {'id': 'legacy', 'confirmed_id': 'legacy'})
            assert len(command('state')['tasks']) == 1
            command("shutdown")
            process.wait(timeout=10)
            assert process.returncode == 0
            assert not Path(address).exists()
            print("Packaged engine: IPC, account isolation, multi-task save/preview/delete, sample persistence, results, setup, search, pause, backup/export, local cleanup, copying/templates, multi-case validation, issue actions and shutdown passed")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
