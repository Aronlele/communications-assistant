"""只读生成异常及下一步入口，不自动恢复发送或重试未知结果。"""
import time


def issues(service):
    rows = []
    def add(code, title, detail, steps, action):
        rows.append(dict(code=code, title=title, detail=detail, steps=steps, action=action))
    if service.auth == 'identity':
        add('identity', '账号等待确认', '已找到登录状态，尚未确认身份。', '核对账号后进入应用，再选择恢复原有任务。', 'setup')
    elif service.account and service.auth not in ('ready', 'connecting'):
        add('connection', '账号尚未连接', '离线期间不能实时接收提示或发送回复。', '检查网络和 Telegram 官方客户端是否可用，在账号页重新连接；若登录失效，重新登录。', 'account')
    if not service.account: return rows
    cfg = service.settings()
    if service.error:
        add('engine', '运行状态需要核对', service.error, '先预览诊断和活动记录，确认原因；发送结果不明时不要重复发送。', 'diagnostics')
    if service.cooldown > time.time():
        add('cooldown', 'Telegram 限流等待', f'仍需等待约 {int(service.cooldown-time.time())+1} 秒。', '保持连接，等待限流结束；不要连续登录或手动重试发送。', 'activity')
    if service.quota_paused:
        add('storage', '本地存储已暂停', '容量或磁盘写入问题可能影响归档及依赖账本的新发送。', '检查可用磁盘和存储权限，预览清理范围或调整归档容量；勿删除发送账本。', 'storage')
    if cfg['pause_all'] or cfg['pause_sends']:
        add('paused', '账号发送处于暂停', '任务开启不代表当前可以发送。', '在任务管理核对暂停原因后自行恢复；本入口不会解除暂停。', 'tasks')
    if cfg.get('resume_tasks'):
        add('recovery', '原任务等待恢复', f"{len(cfg['resume_tasks'])} 个任务需要确认。", '在准备与恢复中核对账号、对象和任务，再选择恢复。', 'setup')
    attention = service.store.results(service.account, status='attention')
    if attention['count']:
        add('results', '发送或回执需要核对', f"{attention['count']} 条结果待核对。", '打开需关注结果并查看原会话。没有同编号回执时不能确认登记，结果未知时不自动重发。', 'results')
    return rows
