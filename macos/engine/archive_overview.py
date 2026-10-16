"""本地归档范围说明；不连接平台或推断已删除历史。"""

def overview(service):
    cfg = service.settings()
    rows = []
    for row in service.store.db.execute("""SELECT c.*, COUNT(m.id) AS count, MIN(m.date) AS earliest, MAX(m.date) AS latest
        FROM conversations c LEFT JOIN messages m ON m.account=c.account AND m.chat=c.chat AND m.deleted=0
        WHERE c.account=? GROUP BY c.chat ORDER BY c.selected DESC,c.title""", (service.account,)):
        value = dict(row)
        initial = service.store.db.execute("SELECT start,detail FROM ranges WHERE account=? AND chat=? AND kind='initial' ORDER BY id LIMIT 1", (service.account, row['chat'])).fetchone()
        value['started'] = initial['start'] if initial else row['initial_start']
        value['basis'] = initial['detail'] if initial else '起始方式尚无已完成记录'
        if not row['selected']: status = '已停止归档，保留本地记录'
        elif cfg['pause_all'] or cfg['archive_paused']: status = '归档已暂停'
        elif service.quota_paused: status = '存储受限，归档已暂停'
        elif service.auth != 'ready' or not (service.demo or service.client and service.client.is_connected()): status = '当前离线，不能接收新记录'
        elif row['chat'] in service.syncing_chats: status = '正在分批同步，条数不代表平台剩余数量'
        elif not row['initialized']: status = '等待首次同步完成'
        else: status = '已选择归档，等待新消息'
        value['status'] = status
        rows.append(value)
    return {'rows': rows, 'note': '时间范围仅表示本机现存记录；受开始范围、保留期、清理及平台可访问性影响，不代表完整聊天历史。'}
