import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.engine_probe import connect_engine


class EngineProbeTests(unittest.TestCase):
    def test_refused_connection_is_closed_before_retry(self):
        first, second = Mock(), Mock()
        first.connect.side_effect = ConnectionRefusedError()
        process = Mock(); process.poll.return_value = None
        with patch('scripts.engine_probe.socket.socket', side_effect=[first, second]), patch('scripts.engine_probe.time.sleep'):
            self.assertIs(connect_engine('/synthetic.sock', process), second)
        first.close.assert_called_once()
        second.close.assert_not_called()
        first.sendall.assert_not_called()

    def test_deadline_exhaustion_does_not_return_unconnected_socket(self):
        client = Mock(); client.connect.side_effect = FileNotFoundError()
        process = Mock(); process.poll.return_value = None
        with patch('scripts.engine_probe.socket.socket', return_value=client), \
             patch('scripts.engine_probe.time.monotonic', side_effect=[0, 0, 2]), \
             patch('scripts.engine_probe.time.sleep'):
            with self.assertRaisesRegex(TimeoutError, '未发送探测请求'):
                connect_engine('/synthetic.sock', process, timeout=1)
        client.close.assert_called_once()
        client.makefile.assert_not_called()
        client.sendall.assert_not_called()

    def test_child_exit_stops_wait_immediately(self):
        process = Mock(returncode=7); process.poll.return_value = 7
        with patch('scripts.engine_probe.socket.socket') as factory:
            with self.assertRaisesRegex(RuntimeError, '状态 7'):
                connect_engine('/synthetic.sock', process)
        factory.assert_not_called()

    def test_startup_longer_than_old_five_second_budget_connects(self):
        with tempfile.TemporaryDirectory(prefix='tga-probe-') as folder:
            address = str(Path(folder) / 's')
            source = ('import socket,sys,time\n'
                      'time.sleep(5.2)\n'
                      's=socket.socket(socket.AF_UNIX);s.bind(sys.argv[1]);s.listen(1)\n'
                      'c,_=s.accept();c.sendall(b"ready");c.close();s.close()\n')
            process = subprocess.Popen([sys.executable, '-c', source, address],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            started = time.monotonic()
            try:
                with connect_engine(address, process, timeout=8) as client:
                    client.settimeout(1)
                    self.assertEqual(client.recv(5), b'ready')
                self.assertGreater(time.monotonic()-started, 5)
                process.wait(timeout=2)
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=2)
