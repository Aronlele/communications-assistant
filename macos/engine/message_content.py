"""群组服务消息转为可搜索文字；不推断平台未返回的身份或事件。"""
from telethon import events, types


class GroupServiceMessage(events.NewMessage):
    @classmethod
    def build(cls, update, others=None, self_id=None):
        if isinstance(update, (types.UpdateNewMessage, types.UpdateNewChannelMessage)) and isinstance(update.message, types.MessageService):
            return cls.Event(update.message)


def entity_name(entity):
    return (' '.join(filter(None, (getattr(entity, 'first_name', ''), getattr(entity, 'last_name', '')))) or
            getattr(entity, 'title', '') or '') if entity else ''


def service_content(message, actor=''):
    action = getattr(message, 'action', None)
    if action is None: return None
    if isinstance(getattr(message, 'peer_id', None), types.PeerUser): return None
    names = {getattr(e, 'id', None): entity_name(e) for e in (getattr(message, 'action_entities', None) or []) if e}
    def member(identifier): return names.get(identifier) or f'成员 ID {identifier}'
    sender = getattr(message, 'sender_id', None)
    actor = actor or entity_name(getattr(message, 'sender', None)) or (member(sender) if sender else '成员（身份不可用）')
    kind = type(action).__name__
    if kind == 'MessageActionChatAddUser':
        users = getattr(action, 'users', [])
        joined = '、'.join(member(i) for i in users) or '成员（身份不可用）'
        text = joined+'加入群组（操作者不可用）' if not sender else actor+'加入群组' if users == [sender] else actor+'邀请'+joined+'加入群组'
    elif kind == 'MessageActionChatDeleteUser':
        text = member(action.user_id)+'离开或被移出群组（操作者不可用）' if not sender else actor+'退出群组' if action.user_id == sender else actor+'将'+member(action.user_id)+'移出群组'
    elif kind == 'MessageActionChatEditTitle': text = actor+'将群组名称修改为「'+action.title+'」'
    elif kind == 'MessageActionChatJoinedByLink': text = actor+'通过邀请链接加入群组'
    elif kind == 'MessageActionChatJoinedByRequest': text = actor+'通过入群申请加入群组'
    elif kind in ('MessageActionChatCreate', 'MessageActionChannelCreate'): text = actor+'创建群组「'+action.title+'」'
    elif kind == 'MessageActionChatEditPhoto': text = actor+'更换群组头像'
    elif kind == 'MessageActionChatDeletePhoto': text = actor+'移除群组头像'
    elif kind == 'MessageActionPinMessage': text = actor+'置顶一条消息'
    elif kind == 'MessageActionHistoryClear': text = actor+'清空群组历史记录'
    elif kind == 'MessageActionChatMigrateTo': text = actor+'将群组升级为超级群'
    elif kind == 'MessageActionChannelMigrateFrom': text = '群组「'+action.title+'」已升级为超级群'
    elif kind == 'MessageActionGroupCall': text = actor+('结束群组通话' if getattr(action, 'duration', None) is not None else '发起群组通话')
    elif kind == 'MessageActionGroupCallScheduled': text = actor+'安排群组通话'
    elif kind == 'MessageActionInviteToGroupCall': text = actor+'邀请'+('、'.join(member(i) for i in action.users) or '成员')+'参与群组通话'
    elif kind == 'MessageActionSetMessagesTTL': text = actor+(f'将消息自动删除时间设为{action.period}秒' if action.period else '关闭消息自动删除')
    elif kind == 'MessageActionTopicCreate': text = actor+'创建话题「'+action.title+'」'
    elif kind == 'MessageActionTopicEdit':
        changes = []
        if getattr(action, 'title', None) is not None: changes.append('将话题名称修改为「'+action.title+'」')
        if getattr(action, 'closed', None) is not None: changes.append('关闭话题' if action.closed else '重新开启话题')
        if getattr(action, 'hidden', None) is not None: changes.append('隐藏话题' if action.hidden else '显示话题')
        text = actor+'、'.join(changes or ['更新话题设置'])
    elif kind == 'MessageActionGameScore': text = actor+f'更新游戏得分为{action.score}'
    else: text = '群组系统事件（此类型暂未支持详细说明）'
    return {'text': text, 'attachment': '群组系统消息'}
