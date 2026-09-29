#!/usr/bin/env python3
"""Save the actual PHLX Semiconductor Sector Index quote for the static site."""
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SOURCE = 'https://hq.sinajs.cn/list=gb_sox'
request = urllib.request.Request(SOURCE, headers={
    'User-Agent': 'Mozilla/5.0 (compatible; USARadar/1.0)',
    'Referer': 'https://finance.sina.com.cn/',
})
with urllib.request.urlopen(request, timeout=20) as response:
    raw = response.read().decode('gb18030')
match = re.search(r'var hq_str_gb_sox="([^"]+)"', raw)
if not match:
    raise RuntimeError('SOX index quote missing; preserving previous file')
fields = match.group(1).split(',')
if len(fields) < 30 or '半导体' not in fields[0]:
    raise RuntimeError('Unexpected SOX index quote; preserving previous file')
close = float(fields[1])
change_percent = float(fields[2])
year = int(fields[29])
market_date = datetime.strptime(f'{fields[25][:6]} {year}', '%b %d %Y').date().isoformat()
if close <= 0 or not -30 < change_percent < 30:
    raise RuntimeError('Invalid SOX price or daily change')
result = {'symbol': 'SOX', 'name': 'PHLX Semiconductor Sector Index',
          'date': market_date, 'close': close, 'changePercent': change_percent,
          'updatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
          'source': 'Sina Finance SOX index quote'}
path = Path(__file__).resolve().parents[1] / 'sox.json'
if path.exists():
    old = json.loads(path.read_text(encoding='utf-8'))
    if (old.get('date'), old.get('close'), old.get('changePercent')) == (market_date, close, change_percent):
        print('SOX quote unchanged')
        raise SystemExit(0)
path.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
print(result)
