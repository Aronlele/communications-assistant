from html.parser import HTMLParser
from pathlib import Path
import plistlib


class Site(HTMLParser):
    def __init__(self):
        super().__init__(); self.refs = []; self.headings = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'h1': self.headings += 1
        for key in ('src', 'href'):
            if values.get(key): self.refs.append(values[key])


root = Path(__file__).resolve().parents[1]
folder = root / 'docs'
text = (folder / 'index.html').read_text()
site = Site(); site.feed(text)
assert site.headings == 1, '展示页需有一个主标题'
version = plistlib.loads((root / 'macos/Info.plist').read_bytes())['CFBundleShortVersionString']
assert version in text and f'/releases/tag/v{version}' in text, '展示版本和下载标签需与应用一致'
for ref in site.refs:
    if ref.startswith(('https://', 'mailto:', '#', 'data:')): continue
    path = (folder / ref.split('#')[0]).resolve()
    assert path.is_relative_to(folder.resolve()) and path.is_file(), f'展示页资源缺失：{ref}'
assert '仅仓库授权成员' in text and '演示' in text
print('展示页版本、资源与私有下载说明检查通过')
