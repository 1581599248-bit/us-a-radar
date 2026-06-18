#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股-A股映射雷达站 v5 — 42板块+极高密度+红色主题+指数映射
"""
import json, os, time, re, threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template_string

APP_DIR = Path(__file__).parent
REFRESH_INTERVAL = 300

with open(APP_DIR / "mapping.json", "r", encoding="utf-8") as f:
    MAPPING = json.load(f)

INDEX_CONFIG = [
    {"usTicker": "^SOX", "usName": "SOX", "usLabel": "费城半导体", "aCode": "sh512480", "aName": "半导体ETF", "aLabel": "半导体"},
    {"usTicker": "^NDX", "usName": "NDX", "usLabel": "纳指100", "aCode": "sz399006", "aName": "创业板指", "aLabel": "创业板"},
    {"usTicker": "^GSPC", "usName": "SPX", "usLabel": "标普500", "aCode": "sh000300", "aName": "沪深300", "aLabel": "沪深300"},
    {"usTicker": "^DJI", "usName": "DJI", "usLabel": "道琼斯", "aCode": "sh000001", "aName": "上证指数", "aLabel": "上证"},
]

class Fetcher:
    def __init__(self):
        self.cache = {"usDate": "", "aDate": "", "indices": [], "sectors": []}
        self.lock = threading.Lock()
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def _fetch_us(self, tickers):
        results = {}
        for t in tickers:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?interval=1d&range=5d"
                r = requests.get(url, headers=self.headers, timeout=10)
                data = r.json()["chart"]["result"][0]
                c = data["indicators"]["quote"][0].get("close", [])
                if len(c) >= 2 and c[-1] and c[-2]:
                    cur, prev = c[-1], c[-2]
                    pct = (cur - prev) / prev * 100
                    results[t] = {"price": round(cur, 2), "prevClose": round(prev, 2), "change": round(cur - prev, 2), "changePercent": round(pct, 2), "status": "ok"}
                else:
                    results[t] = {"error": "no data"}
            except Exception as e:
                results[t] = {"error": str(e)}
            time.sleep(0.05)
        return results

    def _fetch_a(self, stocks):
        qlist = ",".join(f"{s['market'].lower()}{s['code']}" for s in stocks)
        url = f"http://qt.gtimg.cn/q={qlist}"
        r = requests.get(url, headers=self.headers, timeout=15)
        text = r.content.decode("gbk", errors="ignore")
        results = {}
        for line in text.split(";"):
            line = line.strip()
            if not line or not line.startswith("v_"):
                continue
            m = re.search(r'v_(\w+)="(.+)"', line)
            if not m:
                continue
            fields = m.group(2).split("~")
            if len(fields) < 5:
                continue
            code, name, price, prev = fields[2], fields[1], float(fields[3]), float(fields[4])
            change = price - prev
            pct = (change / prev) * 100 if prev else 0
            results[code] = {"code": code, "name": name, "price": round(price, 2), "prevClose": round(prev, 2), "change": round(change, 2), "changePercent": round(pct, 2), "status": "ok"}
        return results

    def _fetch_indices(self):
        indices = {}
        us_tickers = [idx["usTicker"] for idx in INDEX_CONFIG]
        us_data = self._fetch_us(us_tickers)
        for idx in INDEX_CONFIG:
            t = idx["usTicker"]
            info = us_data.get(t, {})
            if info.get("status") == "ok":
                indices[t] = {"price": info["price"], "change": info["change"], "changePercent": info["changePercent"], "status": "ok"}
        a_codes = [{"market": idx["aCode"][:2].upper(), "code": idx["aCode"][2:]} for idx in INDEX_CONFIG]
        a_data = self._fetch_a(a_codes)
        for idx in INDEX_CONFIG:
            ac = idx["aCode"][2:]
            info = a_data.get(ac, {})
            if info.get("status") == "ok":
                indices[idx["aCode"]] = {"price": info["price"], "change": info["change"], "changePercent": info["changePercent"], "status": "ok"}
        return indices

    def refresh(self):
        print(f"[{datetime.now()}] 开始刷新...")
        all_us = set()
        all_a = []
        for s in MAPPING["sectors"]:
            for u in s.get("usLeaders", []):
                all_us.add(u["ticker"])
            for a in s.get("aTargets", []):
                all_a.append(a)
        seen = set()
        uniq_a = [s for s in all_a if not (s["code"] in seen or seen.add(s["code"]))]
        us_data = self._fetch_us(list(all_us))
        a_data = self._fetch_a(uniq_a)
        indices_data = self._fetch_indices()

        # Build v5 format
        now = datetime.now(timezone(timedelta(hours=8)))
        us_date = now.strftime("%Y-%m-%d")
        yesterday = now - timedelta(days=1)
        a_date = yesterday.strftime("%Y-%m-%d")

        # Indices
        indices = []
        for idx in INDEX_CONFIG:
            us_info = indices_data.get(idx["usTicker"], {})
            a_info = indices_data.get(idx["aCode"], {})
            us_chg = us_info.get("changePercent", 0) if us_info.get("status") == "ok" else 0
            a_chg = a_info.get("changePercent", 0) if a_info.get("status") == "ok" else 0
            indices.append({
                "name": f"{idx['usName']} ↔ {idx['aLabel']}",
                "pair": f"{idx['usName']} ↔ {idx['aLabel']}",
                "chg": round(us_chg, 2),
                "desc": f"{idx['usLabel']} vs A股{idx['aLabel']}"
            })

        # Sectors
        sectors = []
        for sector in MAPPING["sectors"]:
            if sector.get("type") == "index":
                continue
            tw = sum(u.get("weight", 1) for u in sector.get("usLeaders", []))
            wp = 0.0
            us_stocks = []
            for u in sector.get("usLeaders", []):
                info = us_data.get(u["ticker"], {})
                p = info.get("changePercent")
                if p is not None:
                    wp += p * u.get("weight", 1)
                name = u.get("name", u["ticker"])
                us_stocks.append({
                    "code": u["ticker"],
                    "name": name,
                    "chg": round(info.get("changePercent", 0), 2) if info.get("status") == "ok" else 0
                })
            wp = wp / tw if tw else 0

            a_stocks = []
            for a in sector.get("aTargets", []):
                info = a_data.get(a["code"], {})
                a_stocks.append({
                    "code": a["code"],
                    "name": a["name"],
                    "chg": round(info.get("changePercent", 0), 2) if info.get("status") == "ok" else 0
                })
            # Sort A-shares by change desc
            a_stocks.sort(key=lambda x: x["chg"], reverse=True)

            direction = "up" if wp >= 2 else "down" if wp <= -2 else "neutral"
            sectors.append({
                "name": sector["name"],
                "weightedChg": round(wp, 2),
                "direction": direction,
                "usStocks": us_stocks,
                "aStocks": a_stocks
            })

        sectors.sort(key=lambda x: abs(x["weightedChg"]), reverse=True)

        with self.lock:
            self.cache = {"usDate": us_date, "aDate": a_date, "indices": indices, "sectors": sectors}
        print(f"[{datetime.now()}] 刷新完成 板块={len(sectors)} 美股={len(us_data)} A股={len(a_data)} 指数={len(indices_data)}")

fetcher = Fetcher()
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/api/data")
def api_data():
    with fetcher.lock:
        return jsonify(fetcher.cache)

def _bg():
    while True:
        try:
            fetcher.refresh()
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(REFRESH_INTERVAL)

HTML_PAGE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>美股-A股映射雷达站</title>
<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
<style>
/* Reset */
*{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Microsoft YaHei,SimHei,sans-serif;}
html,body{width:100%;min-height:100vh;background:#ffffff;}

/* Typography */
body{font-size:12px;line-height:1.15;color:#212121;}

/* Header */
.page-header{
  background:#b71c1c;
  color:#fff;
  padding:6px 8px;
  text-align:center;
  border-bottom:2px solid #7f0000;
}
.page-header h1{
  font-size:15px;
  font-weight:700;
  letter-spacing:0.5px;
  margin:0;
  line-height:1.2;
}
.page-header .sub{
  font-size:10px;
  opacity:0.9;
  margin-top:2px;
  line-height:1.2;
}
.page-header .dates{
  font-size:10px;
  margin-top:3px;
  opacity:0.85;
  display:flex;
  justify-content:center;
  gap:12px;
  flex-wrap:wrap;
  line-height:1.2;
}
.page-header .dates span{
  background:rgba(255,255,255,0.15);
  padding:1px 5px;
  border-radius:0;
  white-space:nowrap;
}

/* Controls bar */
.controls{
  display:flex;
  gap:4px;
  padding:4px 6px;
  background:#f5f5f5;
  border-bottom:1px solid #e0e0e0;
  align-items:center;
  flex-wrap:wrap;
}
.controls input[type="text"]{
  flex:1;min-width:90px;
  font-size:11px;padding:2px 5px;
  border:1px solid #ccc;background:#fff;
  height:22px;
}
.controls select{
  font-size:11px;padding:2px 4px;
  border:1px solid #ccc;background:#fff;
  height:22px;
}
.controls button{
  font-size:11px;padding:2px 8px;
  border:1px solid #7f0000;background:#c62828;color:#fff;
  cursor:pointer;height:22px;
  font-weight:600;
}
.controls button:hover{background:#b71c1c;}

/* Summary strip */
.summary-strip{
  display:flex;
  gap:2px;
  padding:3px 6px;
  background:#fafafa;
  border-bottom:1px solid #e0e0e0;
  font-size:11px;
  justify-content:center;
  flex-wrap:wrap;
}
.summary-strip .chip{
  padding:1px 6px;
  border:1px solid #ddd;
  background:#fff;
  white-space:nowrap;
}
.summary-strip .chip strong{font-size:12px;}
.summary-strip .chip.up{color:#c62828;border-color:#ffcdd2;}
.summary-strip .chip.mid{color:#757575;border-color:#e0e0e0;}
.summary-strip .chip.down{color:#2e7d32;border-color:#c8e6c9;}

/* Index mapping area */
.index-map{
  display:grid;
  grid-template-columns:repeat(4,1fr);
  gap:3px;
  padding:4px 6px;
  border-bottom:1px solid #e0e0e0;
  background:#fafafa;
}
.index-card{
  border:1px solid #e0e0e0;
  background:#fff;
  padding:3px 4px;
  text-align:center;
}
.index-card .pair{
  font-size:10px;font-weight:700;color:#b71c1c;
  line-height:1.1;
}
.index-card .name{
  font-size:10px;color:#616161;
  line-height:1.1;
  margin-top:1px;
}
.index-card .chg{
  font-size:11px;font-weight:700;
  line-height:1.1;
  margin-top:2px;
}
.index-card .chg.up{color:#c62828;}
.index-card .chg.down{color:#2e7d32;}
.index-card .arrow{font-size:10px;}

/* Sector Grid */
.sector-grid{
  display:grid;
  grid-template-columns:repeat(3,1fr);
  gap:4px;
  padding:4px 6px;
}
@media(max-width:900px){
  .sector-grid{grid-template-columns:repeat(2,1fr);}
}
@media(max-width:540px){
  .sector-grid{grid-template-columns:1fr;}
}

/* Sector block */
.sector-block{
  border:1px solid #e0e0e0;
  background:#fff;
}
.sector-header{
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:3px 5px;
  background:#b71c1c;
  color:#fff;
  font-size:12px;
  font-weight:700;
  line-height:1.2;
  min-height:20px;
}
.sector-header .right{
  display:flex;align-items:center;gap:6px;font-size:10px;font-weight:400;
}
.sector-header .wchg{
  font-weight:700;
}
.sector-header .mapdir{
  font-weight:700;
  padding:0 3px;
  border:1px solid rgba(255,255,255,0.5);
}

/* Table inside sector */
.sector-table{
  width:100%;
  border-collapse:collapse;
  font-size:11px;
  table-layout:fixed;
}
.sector-table th, .sector-table td{
  padding:1px 3px;
  border:1px solid #e0e0e0;
  text-align:left;
  vertical-align:middle;
  line-height:1.15;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
}
.sector-table thead th{
  background:#f5f5f5;
  font-weight:600;
  color:#424242;
  font-size:10px;
  padding:1px 3px;
  border-bottom:1px solid #ccc;
}
/* Column widths */
.sector-table .col-tag{width:28px;text-align:center;}
.sector-table .col-code{width:52px;}
.sector-table .col-name{width:auto;}
.sector-table .col-chg{width:56px;text-align:right;}
.sector-table .col-rank{width:26px;text-align:center;}
.sector-table .col-note{width:36px;text-align:center;}

.sector-table tbody tr:nth-child(odd){background:#fafafa;}
.sector-table tbody tr:nth-child(even){background:#fff;}
.sector-table td.chg-red{color:#c62828;font-weight:700;}
.sector-table td.chg-green{color:#2e7d32;font-weight:700;}
.sector-table td.chg-gray{color:#757575;font-weight:700;}
.sector-table .tag{font-size:9px;padding:0 2px;border:1px solid #ddd;font-weight:700;}
.sector-table .tag.us{color:#b71c1c;background:#ffebee;border-color:#ef9a9a;}
.sector-table .tag.a{color:#1565c0;background:#e3f2fd;border-color:#90caf9;}

/* Flag before code for A-share */
.sector-table .flag-a{color:#1565c0;font-weight:700;font-size:10px;}
.sector-table .flag-us{color:#b71c1c;font-weight:700;font-size:10px;}

/* Section divider row in table (between US and A stocks) */
.sector-table .divider td{
  border-top:1px dashed #bbb;
  padding:0;
  height:1px;
  background:transparent;
}

/* A-shares note */
.a-note{
  font-size:9px;color:#757575;padding:1px 5px;
  background:#f5f5f5;border-top:1px solid #e0e0e0;
  text-align:right;
}

/* Footer / Export */
.export-bar{
  padding:4px 6px;
  background:#fafafa;
  border-top:1px solid #e0e0e0;
  display:flex;gap:4px;justify-content:center;flex-wrap:wrap;
}
.export-bar button{
  font-size:11px;padding:2px 10px;
  border:1px solid #7f0000;background:#c62828;color:#fff;
  cursor:pointer;font-weight:600;
  height:22px;
}

/* Snapshot hidden */
.hidden{display:none;}
</style>
</head>
<body>

<!-- Header -->
<div class="page-header">
  <h1>美股昨夜涨跌 &rarr; A股今日映射指引</h1>
  <div class="sub">美股已收盘 / A股未开盘（前一日数据）</div>
  <div class="dates" id="datesLine"></div>
</div>

<!-- Controls -->
<div class="controls">
  <input type="text" id="searchInput" placeholder="搜索标的/代码/板块...">
  <select id="filterSelect">
    <option value="all">全部映射</option>
    <option value="up">▲ 映射利好</option>
    <option value="neutral">— 映射中性</option>
    <option value="down">▼ 映射利空</option>
  </select>
  <button onclick="doExport()">导出截图</button>
</div>

<!-- Summary -->
<div class="summary-strip" id="summaryStrip"></div>

<!-- Index Mapping -->
<div class="index-map" id="indexMap"></div>

<!-- Sector Grid -->
<div class="sector-grid" id="sectorGrid"></div>

<!-- Export bar -->
<div class="export-bar">
  <button onclick="doExport()">导出 PNG 截图</button>
</div>

<script>
/* ==================== Data ==================== */
let DATA = null;
async function loadData() {
  try {
    const r = await fetch('/api/data');
    DATA = await r.json();
    render();
  } catch(e) { console.error('API Error:', e); }
}

/* ==================== Helpers ==================== */
const fmtChg = (n) => (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
const fmtNum = (n) => n.toFixed(2);
const dirIcon = (d) => d === 'up' ? '▲' : d === 'down' ? '▼' : '—';
const dirColor = (d) => d === 'up' ? 'c62828' : d === 'down' ? '2e7d32' : '757575';
const dirClass = (d) => d > 0 ? 'chg-red' : d < 0 ? 'chg-green' : 'chg-gray';
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
};
const yesterdayStr = () => {
  const d = new Date(); d.setDate(d.getDate()-1);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
};

/* ==================== Render ==================== */
function render(){
  // Dates
  const usDate = DATA.usDate || todayStr();
  const aDate = DATA.aDate || yesterdayStr();
  document.getElementById('datesLine').innerHTML = `
    <span>美股：${usDate}（已收盘）</span>
    <span>A股：${aDate}（前一日收盘）</span>
  `;

  // Summary
  const counts = {up:0,neutral:0,down:0};
  (DATA.sectors || []).forEach(s=>{counts[s.direction]++;});
  document.getElementById('summaryStrip').innerHTML = `
    <div class="chip up"><strong>▲ ${counts.up}</strong> 映射利好</div>
    <div class="chip mid"><strong>— ${counts.neutral}</strong> 映射中性</div>
    <div class="chip down"><strong>▼ ${counts.down}</strong> 映射利空</div>
    <div class="chip">板块共 ${(DATA.sectors || []).length} 个</div>
  `;

  // Index map
  const idxHtml = (DATA.indices || []).map(idx => {
    const cls = idx.chg > 0 ? 'up' : idx.chg < 0 ? 'down' : '';
    const arr = idx.chg > 0 ? '▲' : idx.chg < 0 ? '▼' : '—';
    return `
      <div class="index-card">
        <div class="pair">${idx.pair}</div>
        <div class="name">${idx.desc}</div>
        <div class="chg ${cls}">${arr} ${fmtChg(idx.chg)}</div>
      </div>
    `;
  }).join('');
  document.getElementById('indexMap').innerHTML = idxHtml;

  // Sector grid
  const search = document.getElementById('searchInput').value.trim().toLowerCase();
  const filter = document.getElementById('filterSelect').value;

  let filtered = (DATA.sectors || []);
  if (filter !== 'all') filtered = filtered.filter(s => s.direction === filter);
  if (search) {
    filtered = filtered.filter(s => {
      const hay = (s.name + ' ' + s.usStocks.map(x=>x.code+' '+x.name).join(' ') + ' ' + s.aStocks.map(x=>x.code+' '+x.name).join(' ')).toLowerCase();
      return hay.includes(search);
    });
  }

  const grid = document.getElementById('sectorGrid');
  grid.innerHTML = '';

  filtered.forEach(sec => {
    // Sort A-shares by changePercent desc
    const sortedA = [...sec.aStocks].sort((a,b)=>b.chg - a.chg);

    const dIcon = dirIcon(sec.direction);
    const dColor = dirColor(sec.direction);
    const wSign = sec.weightedChg >= 0 ? '+' : '';

    const rows = [];
    // US stocks
    sec.usStocks.forEach((st,i) => {
      rows.push(`
        <tr>
          <td class="col-tag"><span class="tag us">美</span></td>
          <td class="col-code"><span class="flag-us">${st.code}</span></td>
          <td class="col-name">${st.name}</td>
          <td class="col-chg ${dirClass(st.chg)}">${fmtChg(st.chg)}</td>
          <td class="col-note">—</td>
        </tr>
      `);
    });
    // Divider
    if (sec.aStocks.length) {
      rows.push(`<tr class="divider"><td colspan="5"></td></tr>`);
    }
    // A stocks sorted
    sortedA.forEach((st, idx) => {
      rows.push(`
        <tr>
          <td class="col-tag"><span class="tag a">A</span></td>
          <td class="col-code"><span class="flag-a">${st.code}</span></td>
          <td class="col-name">${st.name}</td>
          <td class="col-chg ${dirClass(st.chg)}">${fmtChg(st.chg)}</td>
          <td class="col-note" style="color:#b71c1c;font-weight:700">${idx+1}</td>
        </tr>
      `);
    });

    const block = document.createElement('div');
    block.className = 'sector-block';
    block.innerHTML = `
      <div class="sector-header">
        <span>${sec.name}</span>
        <span class="right">
          <span class="wchg">${wSign}${fmtNum(sec.weightedChg)}%</span>
          <span class="mapdir" style="border-color:rgba(255,255,255,0.6)">${dIcon}</span>
        </span>
      </div>
      <table class="sector-table">
        <thead>
          <tr>
            <th class="col-tag">市</th>
            <th class="col-code">代码</th>
            <th class="col-name">名称</th>
            <th class="col-chg">涨跌</th>
            <th class="col-note">排</th>
          </tr>
        </thead>
        <tbody>${rows.join('')}</tbody>
      </table>
      <div class="a-note">A股数据：${aDate} 前一日收盘</div>
    `;
    grid.appendChild(block);
  });
}

/* ==================== Export ==================== */
function doExport(){
  const btn = document.querySelectorAll('.export-bar button, .controls button');
  const bars = document.querySelectorAll('.controls, .export-bar');
  bars.forEach(b=>b.style.display='none');
  document.body.style.margin='0';

  html2canvas(document.body, {
    backgroundColor: '#ffffff',
    scale: 2,
    useCORS: true,
    allowTaint: false,
    logging: false
  }).then(canvas => {
    const link = document.createElement('a');
    link.download = `美A映射_${todayStr()}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
    bars.forEach(b=>b.style.display='');
  }).catch(err => {
    console.error(err);
    alert('导出失败，请重试');
    bars.forEach(b=>b.style.display='');
  });
}

/* ==================== Events ==================== */
document.getElementById('searchInput').addEventListener('input', render);
document.getElementById('filterSelect').addEventListener('change', render);

/* ==================== Init ==================== */
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
'''

if __name__ == "__main__":
    try:
        fetcher.refresh()
    except Exception as e:
        print(f"[WARN] 首次刷新: {e}")
    threading.Thread(target=_bg, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"[INFO] 启动 http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
