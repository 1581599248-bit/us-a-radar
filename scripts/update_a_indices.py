#!/usr/bin/env python3
"""Save the latest completed A-share index session for next morning's comparison."""
import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

codes = ('sh000001', 'sz399006', 'sh000300', 'sh000688')
request = urllib.request.Request('https://qt.gtimg.cn/q=' + ','.join(codes), headers={
    'User-Agent': 'Mozilla/5.0 (compatible; USARadar/1.0)',
    'Referer': 'https://gu.qq.com/',
})
with urllib.request.urlopen(request, timeout=25) as response:
    raw = response.read().decode('gb18030')
indices = {}
dates = set()
for code in codes:
    match = re.search(r'v_' + code + r'="([^"]+)"', raw)
    if not match:
        raise RuntimeError(f'Missing A-share index {code}')
    fields = match.group(1).split('~')
    price, previous = float(fields[3]), float(fields[4])
    if price <= 0 or previous <= 0 or not re.match(r'^\d{8}', fields[30]):
        raise RuntimeError(f'Invalid quote for {code}')
    dates.add(datetime.strptime(fields[30][:8], '%Y%m%d').date().isoformat())
    indices[code] = {'changePercent': round((price / previous - 1) * 100, 4)}
if len(dates) != 1:
    raise RuntimeError('Index quote dates disagree')
date = dates.pop()
if date != datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():
    raise RuntimeError('A-share quote is not from the completed session today')
path = Path(__file__).resolve().parents[1] / 'a-indices.json'
result = {'date': date, 'indices': indices, 'source': 'Tencent Finance A-share index quotes'}
path.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
print(result)
