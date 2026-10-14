"""只读观察产品状态及进程资源，不接入引擎 socket，不读取用户消息或会话。"""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time


def cpu_seconds(value):
    days, clock = value.split('-', 1) if '-' in value else ('0', value)
    parts = [float(part) for part in clock.split(':')]
    if len(parts) not in (2, 3) or any(part < 0 or not math.isfinite(part) for part in parts):
        raise ValueError('无效的进程 CPU 时间')
    return int(days) * 86400 + sum(part * 60 ** index for index, part in enumerate(reversed(parts)))


def interval_cpu(previous, current, maximum_gap):
    if not previous or current['sampled'] - previous['sampled'] > maximum_gap or current.get('stale') or previous.get('stale'):
        return None
    if (current['pid'], current['native_pid']) != (previous['pid'], previous['native_pid']):
        return None
    elapsed = current['sampled'] - previous['sampled']
    consumed = current['cpu_seconds'] - previous['cpu_seconds']
    return consumed / elapsed * 100 if elapsed > 0 and consumed >= 0 else None


def sample(root):
    value = json.loads((root / 'runtime-status.json').read_text())
    allowed = ('version', 'pid', 'started', 'updated', 'auth', 'connected', 'demo', 'quota_paused',
               'cooldown', 'last_sync', 'syncing_count', 'pending_sends', 'error_present')
    result = {key: value.get(key) for key in allowed}
    result['account_count'] = value.get('account_count', 1)
    result['accounts'] = [{key: row.get(key) for key in allowed if key not in ('pid', 'started', 'updated', 'demo')}
                          for row in value.get('accounts', [])]
    result['sampled'] = time.time()
    result['stale'] = result['sampled'] - value['updated'] > 90
    pid = int(value['pid'])
    parent = subprocess.check_output(['ps', '-p', str(pid), '-o', 'ppid='], text=True).strip()
    rows = subprocess.check_output(['ps', '-p', f'{pid},{int(parent)}', '-o', '%cpu=,rss=,time='], text=True).splitlines()
    if len(rows) != 2: raise ValueError('应用或引擎进程缺失')
    result['native_pid'] = int(parent)
    result['cpu_snapshot_percent'] = sum(float(row.split()[0]) for row in rows)
    result['cpu_seconds'] = sum(cpu_seconds(row.split()[2]) for row in rows)
    result['rss_mb'] = sum(int(row.split()[1]) for row in rows) / 1024
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--hours', type=float, default=72)
    parser.add_argument('--interval', type=float, default=60)
    args = parser.parse_args()
    if not 0 < args.hours <= 168 or args.interval < 1: parser.error('观察时长或间隔无效')
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    samples = args.output / 'samples.jsonl'; summary = args.output / 'summary.json'
    started = time.time(); end = started + args.hours * 3600
    pids = set(); count = failures = interruptions = 0; cpus = []; memory = []; previous = started; previous_sample = None
    with samples.open('a', encoding='utf-8') as stream:
        os.chmod(samples, 0o600)
        while time.time() < end:
            now = time.time()
            if now - previous > args.interval * 2 + 30: interruptions += 1
            previous = now
            try:
                result = sample(args.root)
                result['cpu_percent'] = interval_cpu(previous_sample, result, args.interval * 2 + 30)
                previous_sample = result
                pids.add(result['pid']); count += 1; failures += int(result['stale'])
                if result['cpu_percent'] is not None: cpus.append(result['cpu_percent'])
                memory.append(result['rss_mb'])
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                failures += 1; result = {'sampled': now, 'available': False}
                previous_sample = None
            stream.write(json.dumps(result) + '\n'); stream.flush()
            report = {'started': started, 'planned_end': end, 'updated': now, 'complete': False,
                      'samples': count, 'unavailable_or_stale': failures, 'engine_processes_seen': len(pids),
                      'observation_interruptions': interruptions, 'cpu_mean_percent': statistics.mean(cpus) if cpus else None,
                      'rss_max_mb': max(memory) if memory else None,
                      'cpu_measurement': '相邻样本的原生应用与引擎累计 CPU 时间差；跳过睡眠间隔、缺失样本和进程更换',
                      'note': '资源包括原生应用与引擎。异常和观察中断需人工核对；不等同于全部业务验收。'}
            temporary = summary.with_suffix('.tmp')
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)); os.chmod(temporary, 0o600); temporary.replace(summary)
            time.sleep(min(args.interval, max(0, end - time.time())))
    report.update(complete=True, updated=time.time())
    summary.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
