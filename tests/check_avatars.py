import glob
import hashlib
import json
import os
import struct
from pathlib import Path

root = Path(__file__).resolve().parents[1]
assets = root / 'stack.config/mastodon-rss/assets'
workspace = root.parent
feed_accounts = set()
for path in workspace.glob('mastodon-rss-*-stack-module/stack.config/mastodon-rss/feeds.d/*.json'):
    feed_accounts.update(feed['account'] for feed in json.loads(path.read_text())['feeds'])
sources = json.loads((assets / 'sources.json').read_text())
accounts = {item['account'] for item in sources}
assert 'rss_observer' in accounts
assert feed_accounts <= accounts
assert len(sources) == len(accounts)
files = set()
hashes = set()
for item in sources:
    path = assets / item['avatar']
    data = path.read_bytes()
    assert path.name.startswith(item['account'] + '-')
    assert path.name.endswith('.png')
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    assert struct.unpack('>II', data[16:24]) == (512, 512)
    assert hashlib.sha256(data).hexdigest()[:12] == path.stem.rsplit('-', 1)[1]
    assert path.name not in files
    assert hashlib.sha256(data).digest() not in hashes
    files.add(path.name)
    hashes.add(hashlib.sha256(data).digest())
assert {p.name for p in assets.glob('rss_*.png')} == files
print(f'[mastodon-rss-avatars] validated {len(files)} distinct 512px avatars')
