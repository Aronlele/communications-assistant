"""从实际应用生成安装元数据，不猜测版本或系统要求。"""
from pathlib import Path
import plistlib
import re
import sys
import xml.etree.ElementTree as ET


def write_metadata(app, destination):
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    identifier = 'local.communications.assistant'
    if info.get('CFBundleIdentifier') != identifier:
        raise ValueError('应用标识不属于即讯，停止打包')
    if info.get('CFBundleExecutable') != 'CommunicationsAssistant' or not (app / 'Contents/MacOS/CommunicationsAssistant').is_file():
        raise ValueError('应用可执行文件缺失或不匹配')
    version, minimum = info.get('CFBundleShortVersionString'), info.get('LSMinimumSystemVersion')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('应用版本应为三段数字')
    if not isinstance(minimum, str) or not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', minimum):
        raise ValueError('最低系统版本无效')
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'components.plist').write_bytes(plistlib.dumps([{
        'RootRelativeBundlePath': '通讯助手.app', 'BundleIsRelocatable': False,
        'BundleIsVersionChecked': True, 'BundleHasStrictIdentifier': True,
        'BundleOverwriteAction': 'upgrade'}]))
    xml = ET.Element('installer-gui-script', minSpecVersion='2')
    ET.SubElement(xml, 'title').text = info['CFBundleDisplayName'] + ' ' + version
    for tag in ('welcome', 'readme', 'conclusion'):
        ET.SubElement(xml, tag, file=tag + '.html', **{'mime-type': 'text/html'})
    ET.SubElement(xml, 'options', customize='never', **{'require-scripts': 'false', 'hostArchitectures': 'arm64'})
    ET.SubElement(xml, 'domains', enable_anywhere='false', enable_currentUserHome='false', enable_localSystem='true')
    check = ET.SubElement(xml, 'volume-check')
    ET.SubElement(ET.SubElement(check, 'allowed-os-versions'), 'os-version', min=minimum)
    ET.SubElement(ET.SubElement(xml, 'choices-outline'), 'line', choice='assistant')
    choice = ET.SubElement(xml, 'choice', id='assistant', title=info['CFBundleDisplayName'], visible='false')
    ET.SubElement(choice, 'pkg-ref', id=identifier + '.pkg')
    package = ET.SubElement(xml, 'pkg-ref', id=identifier + '.pkg', version=version, onConclusion='None')
    package.text = 'assistant.pkg'
    ET.SubElement(ET.SubElement(ET.SubElement(xml, 'pkg-ref', id=identifier + '.pkg'), 'must-close'), 'app', id=identifier)
    ET.ElementTree(xml).write(destination / 'Distribution.xml', encoding='utf-8', xml_declaration=True)
    return version


if __name__ == '__main__':
    print(write_metadata(Path(sys.argv[1]), Path(sys.argv[2])))
