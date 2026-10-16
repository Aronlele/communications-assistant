"""实际冻结引擎读取合成服务消息与旧空记录；不接触工作根目录或Telegram。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.store import Store
from engine_probe import connect_engine


def main():
    parser=argparse.ArgumentParser();parser.add_argument('executable');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='jixun-service-smoke-') as temporary:
        folder=Path(temporary);root=folder/'data';store=Store(root)
        store.put_conversation(999001,-42,'系统事件合成验收','group');store.select(999001,-42,True)
        store.put_messages(999001,-42,[{'id':100,'date':time.time(),'sender':'合成成员','text':'合成成员邀请新成员加入群组','attachment':'群组系统消息'},
                                    {'id':101,'date':time.time(),'sender':'合成成员','text':'合成成员将群组名称修改为「合成名称」','attachment':'群组系统消息'},
                                    {'id':102,'date':time.time(),'sender':'合成成员','text':''}]);store.close()
        address=folder/'engine.sock'
        process=subprocess.Popen([args.executable,'--root',str(root),'--socket',str(address),'--demo'],env={'PATH':'/usr/bin:/bin'},stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            client=connect_engine(address,process);client.settimeout(15);file=client.makefile('rwb');sequence=0
            def call(method,args=None):
                nonlocal sequence
                sequence+=1;identifier=str(sequence)
                file.write((json.dumps({'protocol':1,'id':identifier,'method':method,'args':args or {}})+'\n').encode());file.flush()
                while True:
                    row=json.loads(file.readline())
                    if row.get('id')==identifier:return row
            state=call('state')['result'];before=state['settings']
            rows=call('search',{'chat':-42})['result']['messages'];assert len(rows)==3
            assert sum(r['attachment']=='群组系统消息' for r in rows)==2
            assert call('search',{'query':'合成名称'})['result']['count']==1
            preview=call('archive_repair_preview',{'chat':-42})['result'];assert preview['count']==preview['total']==1
            result=call('archive_repair_apply',{'token':preview['token'],'confirmed_account':999001});assert '演示' in result['error']
            assert call('state')['result']['settings']==before
            output=folder/'system-events.json';call('export',{'path':str(output),'format':'json'});assert '合成名称' in output.read_text()
            second=state['accounts'][1]['slot'];call('select_account',{'slot':second})
            assert call('search',{'chat':-42})['result']['count']==0
            assert 'error' in call('archive_repair_preview',{'chat':-42})
            call('shutdown');process.wait(timeout=10);assert process.returncode==0
            print('Packaged service messages: searchable descriptions, system labels, old-empty preview, demo/network guard, settings preservation, export and account isolation passed')
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)


if __name__=='__main__':main()
