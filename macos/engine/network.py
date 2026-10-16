"""每账号代理与无登录链路探测；不借用或重连正在工作的Telegram客户端。"""
import asyncio
import json
import socket
import time
import unicodedata

DESTINATION = ('149.154.167.51', 443)


def configuration(value):
    if not isinstance(value, dict): raise ValueError('网络配置无效')
    mode = value.get('mode', 'direct')
    if mode not in ('direct', 'socks5', 'http'): raise ValueError('请选择直连、SOCKS5或HTTP CONNECT')
    if mode == 'direct': return {'mode': 'direct', 'host': '', 'port': 0, 'username': '', 'requires_password': False}
    host, port, user = value.get('host', ''), value.get('port'), value.get('username', '')
    if not isinstance(host, str) or not host or len(host) > 253 or any(c.isspace() or c in '/\\@?#' or unicodedata.category(c).startswith('C') for c in host):
        raise ValueError('代理地址只填主机名或IP，不含协议、账号或路径')
    if type(port) is not int or not 1 <= port <= 65535: raise ValueError('代理端口应为1–65535')
    if not isinstance(user, str) or len(user) > 255 or any(unicodedata.category(c).startswith('C') for c in user): raise ValueError('代理用户名无效')
    required = value.get('requires_password', False)
    if type(required) is not bool: raise ValueError('代理认证选项无效')
    return dict(mode=mode, host=host, port=port, username=user, requires_password=required)


def credentials(value):
    cfg = configuration(value)
    password = value.get('password', '')
    if not isinstance(password, str) or len(password) > 1024 or '\x00' in password: raise ValueError('代理密码格式无效')
    if cfg['mode'] != 'direct' and cfg['requires_password'] and not password:
        raise ValueError('代理密码不可用，请在网络设置重新保存密码')
    return cfg, password


def telethon_proxy(value):
    cfg, password = credentials(value)
    if cfg['mode'] == 'direct': return None
    return dict(proxy_type=cfg['mode'], addr=cfg['host'], port=cfg['port'], rdns=True,
                username=cfg['username'] or None, password=password or None)


async def probe(value, timeout=8):
    cfg, password = credentials(value)
    started = time.monotonic()
    try:
        if cfg['mode'] == 'direct':
            reader, writer = await asyncio.wait_for(asyncio.open_connection(*DESTINATION), timeout)
            writer.close(); await writer.wait_closed()
        else:
            from python_socks import ProxyType
            from python_socks.async_.asyncio import Proxy
            proxy = Proxy(proxy_type=ProxyType.SOCKS5 if cfg['mode'] == 'socks5' else ProxyType.HTTP,
                          host=cfg['host'], port=cfg['port'], username=cfg['username'] or None,
                          password=password or None, rdns=True)
            connection = await proxy.connect(dest_host=DESTINATION[0], dest_port=DESTINATION[1], timeout=timeout)
            connection.close()
        return {'ok': True, 'mode': cfg['mode'], 'latency_ms': round((time.monotonic()-started)*1000),
                'detail': '到Telegram端口的链路可达；未登录、不发送消息，不代表API有效或账号登录成功。'}
    except (OSError, asyncio.TimeoutError) as error:
        reason = '地址解析失败' if isinstance(error, socket.gaierror) else '连接失败或超时'
    except Exception:
        reason = '代理握手或认证未通过'
    return {'ok': False, 'mode': cfg['mode'], 'detail': reason+'。请核对网络、地址、端口和认证；当前账号连接未改变。'}


async def command(service, method, args):
    if method == 'network_probe':
        result = await probe(args.get('proxy', {}))
        return dict(result, account_connected=bool(service.client and service.client.is_connected()),
                    cooldown_until=service.cooldown, login_state=service.auth)
    cfg = configuration(args.get('proxy', {}))
    if service.demo: raise ValueError('演示模式只能测试与预览，不保存真实网络配置')
    if service.auth in ('connecting', 'phone', 'code', 'password', 'identity', 'logging_out'):
        raise ValueError('请先完成或取消登录，再保存网络设置')
    if method == 'network_save':
        service.store.preference('network_proxy', json.dumps(cfg))
        await service.publish()
        return service.state()
    if method == 'network_apply':
        service.require_account(live=True)
        if args.get('confirmed_account') != service.account: raise ValueError('请确认要重新连接的当前账号')
        if service.send_jobs: raise ValueError('自动发送正在进行，请稍后重新连接')
        supplied = args.get('proxy', {})
        telethon_proxy(supplied)
        from engine.tasks import disarm_all
        saved = service.settings(); disarm_all(saved, remember=True)
        service.store.save_settings(service.account, saved)
        service.store.preference('network_proxy', json.dumps(cfg))
        api_id, api_hash = service.client.api_id, service.client.api_hash
        service.auth = 'offline'
        return await service.connect({'api_id': api_id, 'api_hash': api_hash, 'proxy': supplied})
    raise ValueError('不支持的网络操作')
