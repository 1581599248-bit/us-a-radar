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
{
  "version": "5.0",
  "updatedAt": "2026-06-18",
  "sectors": [
    {
      "id": "optical-module",
      "name": "光模块",
      "nameEn": "Optical Modules",
      "usLeaders": [
        {
          "ticker": "LITE",
          "name": "Lumentum / 朗美通",
          "weight": 1.0
        },
        {
          "ticker": "COHR",
          "name": "Coherent / 相干公司",
          "weight": 1.0
        },
        {
          "ticker": "MRVL",
          "name": "Marvell / 迈威尔",
          "weight": 0.8
        },
        {
          "ticker": "AVGO",
          "name": "Broadcom / 博通",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "300308",
          "market": "SZ",
          "name": "中际旭创",
          "weight": 1.0
        },
        {
          "code": "300502",
          "market": "SZ",
          "name": "新易盛",
          "weight": 1.0
        },
        {
          "code": "300394",
          "market": "SZ",
          "name": "天孚通信",
          "weight": 0.8
        },
        {
          "code": "002281",
          "market": "SZ",
          "name": "光迅科技",
          "weight": 0.7
        },
        {
          "code": "300548",
          "market": "SZ",
          "name": "博创科技",
          "weight": 0.6
        },
        {
          "code": "603083",
          "market": "SH",
          "name": "剑桥科技",
          "weight": 0.5
        },
        {
          "code": "300570",
          "market": "SZ",
          "name": "太辰光",
          "weight": 0.5
        },
        {
          "code": "002902",
          "market": "SZ",
          "name": "铭普光磁",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "gpu",
      "name": "GPU/算力芯片",
      "nameEn": "GPU / AI Chips",
      "usLeaders": [
        {
          "ticker": "NVDA",
          "name": "NVIDIA / 英伟达",
          "weight": 1.0
        },
        {
          "ticker": "AMD",
          "name": "AMD / 超威半导体",
          "weight": 1.0
        },
        {
          "ticker": "INTC",
          "name": "Intel / 英特尔",
          "weight": 0.6
        },
        {
          "ticker": "ARM",
          "name": "ARM Holdings / 安谋",
          "weight": 0.5
        }
      ],
      "aTargets": [
        {
          "code": "300474",
          "market": "SZ",
          "name": "景嘉微",
          "weight": 1.0
        },
        {
          "code": "688041",
          "market": "SH",
          "name": "海光信息",
          "weight": 1.0
        },
        {
          "code": "688256",
          "market": "SH",
          "name": "寒武纪",
          "weight": 0.9
        },
        {
          "code": "688047",
          "market": "SH",
          "name": "龙芯中科",
          "weight": 0.7
        },
        {
          "code": "000066",
          "market": "SZ",
          "name": "中国长城",
          "weight": 0.6
        },
        {
          "code": "688525",
          "market": "SH",
          "name": "佰维存储",
          "weight": 0.5
        },
        {
          "code": "300223",
          "market": "SZ",
          "name": "北京君正",
          "weight": 0.5
        },
        {
          "code": "688385",
          "market": "SH",
          "name": "复旦微电",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "semi-equipment",
      "name": "半导体设备",
      "nameEn": "Semiconductor Equipment",
      "usLeaders": [
        {
          "ticker": "AMAT",
          "name": "Applied Materials / 应用材料",
          "weight": 1.0
        },
        {
          "ticker": "LRCX",
          "name": "Lam Research / 拉姆研究",
          "weight": 1.0
        },
        {
          "ticker": "KLAC",
          "name": "KLA / 科磊",
          "weight": 1.0
        },
        {
          "ticker": "ASML",
          "name": "ASML / 阿斯麦",
          "weight": 1.0
        }
      ],
      "aTargets": [
        {
          "code": "002371",
          "market": "SZ",
          "name": "北方华创",
          "weight": 1.0
        },
        {
          "code": "688012",
          "market": "SH",
          "name": "中微公司",
          "weight": 1.0
        },
        {
          "code": "688120",
          "market": "SH",
          "name": "华海清科",
          "weight": 0.8
        },
        {
          "code": "688072",
          "market": "SH",
          "name": "拓荆科技",
          "weight": 0.7
        },
        {
          "code": "688082",
          "market": "SH",
          "name": "盛美上海",
          "weight": 0.6
        },
        {
          "code": "688361",
          "market": "SH",
          "name": "中科飞测",
          "weight": 0.5
        },
        {
          "code": "688409",
          "market": "SH",
          "name": "富创精密",
          "weight": 0.5
        },
        {
          "code": "603690",
          "market": "SH",
          "name": "至纯科技",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "semi-materials",
      "name": "半导体材料/IC设计",
      "nameEn": "Semiconductor Materials / IC Design",
      "usLeaders": [
        {
          "ticker": "AVGO",
          "name": "Broadcom / 博通",
          "weight": 1.0
        },
        {
          "ticker": "QCOM",
          "name": "Qualcomm / 高通",
          "weight": 1.0
        },
        {
          "ticker": "MU",
          "name": "Micron / 美光",
          "weight": 1.0
        },
        {
          "ticker": "TSM",
          "name": "TSMC / 台积电",
          "weight": 1.0
        }
      ],
      "aTargets": [
        {
          "code": "688981",
          "market": "SH",
          "name": "中芯国际",
          "weight": 1.0
        },
        {
          "code": "603501",
          "market": "SH",
          "name": "韦尔股份",
          "weight": 1.0
        },
        {
          "code": "603986",
          "market": "SH",
          "name": "兆易创新",
          "weight": 1.0
        },
        {
          "code": "688008",
          "market": "SH",
          "name": "澜起科技",
          "weight": 0.8
        },
        {
          "code": "688126",
          "market": "SH",
          "name": "沪硅产业",
          "weight": 0.6
        },
        {
          "code": "300782",
          "market": "SZ",
          "name": "卓胜微",
          "weight": 0.7
        },
        {
          "code": "688595",
          "market": "SH",
          "name": "芯海科技",
          "weight": 0.4
        },
        {
          "code": "688396",
          "market": "SH",
          "name": "华润微",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "pcb",
      "name": "PCB",
      "nameEn": "PCB",
      "usLeaders": [
        {
          "ticker": "TTMI",
          "name": "TTM Technologies / 迅达科技",
          "weight": 1.0
        },
        {
          "ticker": "JBL",
          "name": "Jabil / 捷普",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "002463",
          "market": "SZ",
          "name": "沪电股份",
          "weight": 1.0
        },
        {
          "code": "002916",
          "market": "SZ",
          "name": "深南电路",
          "weight": 1.0
        },
        {
          "code": "600183",
          "market": "SH",
          "name": "生益科技",
          "weight": 0.8
        },
        {
          "code": "002938",
          "market": "SZ",
          "name": "鹏鼎控股",
          "weight": 0.7
        },
        {
          "code": "603228",
          "market": "SH",
          "name": "景旺电子",
          "weight": 0.6
        },
        {
          "code": "002815",
          "market": "SZ",
          "name": "崇达技术",
          "weight": 0.5
        },
        {
          "code": "300739",
          "market": "SZ",
          "name": "明阳电路",
          "weight": 0.4
        },
        {
          "code": "603386",
          "market": "SH",
          "name": "骏亚科技",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "liquid-cooling",
      "name": "液冷",
      "nameEn": "Liquid Cooling",
      "usLeaders": [
        {
          "ticker": "VRT",
          "name": "Vertiv / 维谛技术",
          "weight": 1.0
        },
        {
          "ticker": "DELL",
          "name": "Dell / 戴尔",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "002837",
          "market": "SZ",
          "name": "英维克",
          "weight": 1.0
        },
        {
          "code": "300499",
          "market": "SZ",
          "name": "高澜股份",
          "weight": 0.8
        },
        {
          "code": "301018",
          "market": "SZ",
          "name": "申菱环境",
          "weight": 0.6
        },
        {
          "code": "300990",
          "market": "SZ",
          "name": "同飞股份",
          "weight": 0.5
        },
        {
          "code": "002886",
          "market": "SZ",
          "name": "沃特股份",
          "weight": 0.4
        },
        {
          "code": "301286",
          "market": "SZ",
          "name": "同星科技",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "cpo",
      "name": "CPO",
      "nameEn": "CPO (Co-Packaged Optics)",
      "usLeaders": [
        {
          "ticker": "AVGO",
          "name": "Broadcom / 博通",
          "weight": 1.0
        },
        {
          "ticker": "MRVL",
          "name": "Marvell / 迈威尔",
          "weight": 1.0
        },
        {
          "ticker": "NVDA",
          "name": "NVIDIA / 英伟达",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "300308",
          "market": "SZ",
          "name": "中际旭创",
          "weight": 1.0
        },
        {
          "code": "300502",
          "market": "SZ",
          "name": "新易盛",
          "weight": 1.0
        },
        {
          "code": "300394",
          "market": "SZ",
          "name": "天孚通信",
          "weight": 0.8
        },
        {
          "code": "301165",
          "market": "SZ",
          "name": "锐捷网络",
          "weight": 0.5
        },
        {
          "code": "002281",
          "market": "SZ",
          "name": "光迅科技",
          "weight": 0.7
        },
        {
          "code": "300570",
          "market": "SZ",
          "name": "太辰光",
          "weight": 0.5
        },
        {
          "code": "300548",
          "market": "SZ",
          "name": "博创科技",
          "weight": 0.6
        },
        {
          "code": "002902",
          "market": "SZ",
          "name": "铭普光磁",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "server-dc",
      "name": "服务器/数据中心",
      "nameEn": "Servers / Data Center",
      "usLeaders": [
        {
          "ticker": "DELL",
          "name": "Dell / 戴尔",
          "weight": 1.0
        },
        {
          "ticker": "HPE",
          "name": "HPE / 慧与",
          "weight": 0.8
        },
        {
          "ticker": "SMCI",
          "name": "Super Micro / 超微电脑",
          "weight": 1.0
        },
        {
          "ticker": "ANET",
          "name": "Arista Networks / Arista",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "000977",
          "market": "SZ",
          "name": "浪潮信息",
          "weight": 1.0
        },
        {
          "code": "603019",
          "market": "SH",
          "name": "中科曙光",
          "weight": 1.0
        },
        {
          "code": "601138",
          "market": "SH",
          "name": "工业富联",
          "weight": 0.9
        },
        {
          "code": "600498",
          "market": "SH",
          "name": "烽火通信",
          "weight": 0.6
        },
        {
          "code": "000938",
          "market": "SZ",
          "name": "紫光股份",
          "weight": 0.5
        },
        {
          "code": "600728",
          "market": "SH",
          "name": "佳都科技",
          "weight": 0.4
        },
        {
          "code": "300212",
          "market": "SZ",
          "name": "易华录",
          "weight": 0.4
        },
        {
          "code": "600845",
          "market": "SH",
          "name": "宝信软件",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "memory",
      "name": "存储/内存",
      "nameEn": "Memory / Storage",
      "usLeaders": [
        {
          "ticker": "MU",
          "name": "Micron / 美光",
          "weight": 1.0
        },
        {
          "ticker": "WDC",
          "name": "Western Digital / 西部数据",
          "weight": 1.0
        },
        {
          "ticker": "STX",
          "name": "Seagate / 希捷",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "603986",
          "market": "SH",
          "name": "兆易创新",
          "weight": 1.0
        },
        {
          "code": "688525",
          "market": "SH",
          "name": "佰维存储",
          "weight": 0.8
        },
        {
          "code": "688766",
          "market": "SH",
          "name": "普冉股份",
          "weight": 0.7
        },
        {
          "code": "688123",
          "market": "SH",
          "name": "聚辰股份",
          "weight": 0.6
        },
        {
          "code": "300223",
          "market": "SZ",
          "name": "北京君正",
          "weight": 0.6
        },
        {
          "code": "688216",
          "market": "SH",
          "name": "气派科技",
          "weight": 0.4
        },
        {
          "code": "600171",
          "market": "SH",
          "name": "上海贝岭",
          "weight": 0.5
        },
        {
          "code": "688385",
          "market": "SH",
          "name": "复旦微电",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "consumer-electronics",
      "name": "消费电子",
      "nameEn": "Consumer Electronics",
      "usLeaders": [
        {
          "ticker": "AAPL",
          "name": "Apple / 苹果",
          "weight": 1.0
        },
        {
          "ticker": "SONY",
          "name": "Sony / 索尼",
          "weight": 0.8
        },
        {
          "ticker": "GOOGL",
          "name": "Alphabet / 谷歌",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "002475",
          "market": "SZ",
          "name": "立讯精密",
          "weight": 1.0
        },
        {
          "code": "002241",
          "market": "SZ",
          "name": "歌尔股份",
          "weight": 0.9
        },
        {
          "code": "300433",
          "market": "SZ",
          "name": "蓝思科技",
          "weight": 0.8
        },
        {
          "code": "601231",
          "market": "SH",
          "name": "环旭电子",
          "weight": 0.6
        },
        {
          "code": "002600",
          "market": "SZ",
          "name": "领益智造",
          "weight": 0.6
        },
        {
          "code": "300136",
          "market": "SZ",
          "name": "信维通信",
          "weight": 0.5
        },
        {
          "code": "002273",
          "market": "SZ",
          "name": "水晶光电",
          "weight": 0.5
        },
        {
          "code": "603380",
          "market": "SH",
          "name": "易德龙",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "ai-software",
      "name": "AI软件/大模型",
      "nameEn": "AI Software / LLM",
      "usLeaders": [
        {
          "ticker": "MSFT",
          "name": "Microsoft / 微软",
          "weight": 1.0
        },
        {
          "ticker": "GOOGL",
          "name": "Alphabet / 谷歌",
          "weight": 1.0
        },
        {
          "ticker": "META",
          "name": "Meta / Meta",
          "weight": 1.0
        },
        {
          "ticker": "PLTR",
          "name": "Palantir / Palantir",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "300418",
          "market": "SZ",
          "name": "昆仑万维",
          "weight": 1.0
        },
        {
          "code": "300364",
          "market": "SZ",
          "name": "中文在线",
          "weight": 0.8
        },
        {
          "code": "300339",
          "market": "SZ",
          "name": "润和软件",
          "weight": 0.7
        },
        {
          "code": "300229",
          "market": "SZ",
          "name": "拓尔思",
          "weight": 0.6
        },
        {
          "code": "002230",
          "market": "SZ",
          "name": "科大讯飞",
          "weight": 1.0
        },
        {
          "code": "300002",
          "market": "SZ",
          "name": "神州泰岳",
          "weight": 0.5
        },
        {
          "code": "300624",
          "market": "SZ",
          "name": "万兴科技",
          "weight": 0.6
        },
        {
          "code": "300166",
          "market": "SZ",
          "name": "东方国信",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "cloud-saas",
      "name": "云计算/SaaS",
      "nameEn": "Cloud / SaaS",
      "usLeaders": [
        {
          "ticker": "CRM",
          "name": "Salesforce / Salesforce",
          "weight": 1.0
        },
        {
          "ticker": "NET",
          "name": "Cloudflare / Cloudflare",
          "weight": 0.8
        },
        {
          "ticker": "SNOW",
          "name": "Snowflake / Snowflake",
          "weight": 0.8
        },
        {
          "ticker": "NOW",
          "name": "ServiceNow / ServiceNow",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "600588",
          "market": "SH",
          "name": "用友网络",
          "weight": 1.0
        },
        {
          "code": "688111",
          "market": "SH",
          "name": "金山办公",
          "weight": 1.0
        },
        {
          "code": "002410",
          "market": "SZ",
          "name": "广联达",
          "weight": 0.8
        },
        {
          "code": "600845",
          "market": "SH",
          "name": "宝信软件",
          "weight": 0.7
        },
        {
          "code": "300454",
          "market": "SZ",
          "name": "深信服",
          "weight": 0.9
        },
        {
          "code": "300253",
          "market": "SZ",
          "name": "卫宁健康",
          "weight": 0.5
        },
        {
          "code": "300451",
          "market": "SZ",
          "name": "创业慧康",
          "weight": 0.4
        },
        {
          "code": "300624",
          "market": "SZ",
          "name": "万兴科技",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "cybersecurity",
      "name": "网络安全",
      "nameEn": "Cybersecurity",
      "usLeaders": [
        {
          "ticker": "PANW",
          "name": "Palo Alto Networks / 派拓网络",
          "weight": 1.0
        },
        {
          "ticker": "FTNT",
          "name": "Fortinet / 飞塔",
          "weight": 0.9
        },
        {
          "ticker": "CRWD",
          "name": "CrowdStrike / CrowdStrike",
          "weight": 1.0
        }
      ],
      "aTargets": [
        {
          "code": "688561",
          "market": "SH",
          "name": "奇安信",
          "weight": 1.0
        },
        {
          "code": "300454",
          "market": "SZ",
          "name": "深信服",
          "weight": 1.0
        },
        {
          "code": "002439",
          "market": "SZ",
          "name": "启明星辰",
          "weight": 0.9
        },
        {
          "code": "300369",
          "market": "SZ",
          "name": "绿盟科技",
          "weight": 0.6
        },
        {
          "code": "002268",
          "market": "SZ",
          "name": "电科网安",
          "weight": 0.7
        },
        {
          "code": "300188",
          "market": "SZ",
          "name": "美亚柏科",
          "weight": 0.5
        },
        {
          "code": "688225",
          "market": "SH",
          "name": "亚信安全",
          "weight": 0.5
        },
        {
          "code": "300768",
          "market": "SZ",
          "name": "迪普科技",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "new-energy",
      "name": "新能源/光伏/储能",
      "nameEn": "New Energy / Solar / Storage",
      "usLeaders": [
        {
          "ticker": "TSLA",
          "name": "Tesla / 特斯拉",
          "weight": 1.0
        },
        {
          "ticker": "FSLR",
          "name": "First Solar / 第一太阳能",
          "weight": 1.0
        },
        {
          "ticker": "ENPH",
          "name": "Enphase / Enphase",
          "weight": 0.8
        },
        {
          "ticker": "NEE",
          "name": "NextEra Energy / 新纪元能源",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "300750",
          "market": "SZ",
          "name": "宁德时代",
          "weight": 1.0
        },
        {
          "code": "002594",
          "market": "SZ",
          "name": "比亚迪",
          "weight": 1.0
        },
        {
          "code": "601012",
          "market": "SH",
          "name": "隆基绿能",
          "weight": 1.0
        },
        {
          "code": "300274",
          "market": "SZ",
          "name": "阳光电源",
          "weight": 1.0
        },
        {
          "code": "600438",
          "market": "SH",
          "name": "通威股份",
          "weight": 0.8
        },
        {
          "code": "002459",
          "market": "SZ",
          "name": "晶澳科技",
          "weight": 0.7
        },
        {
          "code": "300014",
          "market": "SZ",
          "name": "亿纬锂能",
          "weight": 0.8
        },
        {
          "code": "600885",
          "market": "SH",
          "name": "宏发股份",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "car-battery",
      "name": "动力电池/锂电",
      "nameEn": "EV Battery / Lithium",
      "usLeaders": [
        {
          "ticker": "TSLA",
          "name": "Tesla / 特斯拉",
          "weight": 1.0
        },
        {
          "ticker": "ALB",
          "name": "Albemarle / 雅宝",
          "weight": 1.0
        }
      ],
      "aTargets": [
        {
          "code": "300750",
          "market": "SZ",
          "name": "宁德时代",
          "weight": 1.0
        },
        {
          "code": "002709",
          "market": "SZ",
          "name": "天赐材料",
          "weight": 0.8
        },
        {
          "code": "300014",
          "market": "SZ",
          "name": "亿纬锂能",
          "weight": 0.9
        },
        {
          "code": "002074",
          "market": "SZ",
          "name": "国轩高科",
          "weight": 0.6
        },
        {
          "code": "300073",
          "market": "SZ",
          "name": "当升科技",
          "weight": 0.7
        },
        {
          "code": "002812",
          "market": "SZ",
          "name": "恩捷股份",
          "weight": 0.8
        },
        {
          "code": "603659",
          "market": "SH",
          "name": "璞泰来",
          "weight": 0.7
        },
        {
          "code": "300450",
          "market": "SZ",
          "name": "先导智能",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "copper",
      "name": "铜/资源",
      "nameEn": "Copper / Resources",
      "usLeaders": [
        {
          "ticker": "SCCO",
          "name": "Southern Copper / 南方铜业",
          "weight": 1.0
        },
        {
          "ticker": "FCX",
          "name": "Freeport-McMoRan / 自由港",
          "weight": 1.0
        },
        {
          "ticker": "BHP",
          "name": "BHP / 必和必拓",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "600362",
          "market": "SH",
          "name": "江西铜业",
          "weight": 1.0
        },
        {
          "code": "000630",
          "market": "SZ",
          "name": "铜陵有色",
          "weight": 0.8
        },
        {
          "code": "600497",
          "market": "SH",
          "name": "驰宏锌锗",
          "weight": 0.5
        },
        {
          "code": "000878",
          "market": "SZ",
          "name": "云南铜业",
          "weight": 0.5
        },
        {
          "code": "601899",
          "market": "SH",
          "name": "紫金矿业",
          "weight": 1.0
        },
        {
          "code": "600547",
          "market": "SH",
          "name": "山东黄金",
          "weight": 0.6
        },
        {
          "code": "600489",
          "market": "SH",
          "name": "中金黄金",
          "weight": 0.5
        },
        {
          "code": "601600",
          "market": "SH",
          "name": "中国铝业",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "medical-device",
      "name": "医疗器械",
      "nameEn": "Medical Devices",
      "usLeaders": [
        {
          "ticker": "ISRG",
          "name": "Intuitive Surgical / 直觉外科",
          "weight": 1.0
        },
        {
          "ticker": "SYK",
          "name": "Stryker / 史赛克",
          "weight": 1.0
        },
        {
          "ticker": "MDT",
          "name": "Medtronic / 美敦力",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "300760",
          "market": "SZ",
          "name": "迈瑞医疗",
          "weight": 1.0
        },
        {
          "code": "688617",
          "market": "SH",
          "name": "惠泰医疗",
          "weight": 0.8
        },
        {
          "code": "688580",
          "market": "SH",
          "name": "伟思医疗",
          "weight": 0.5
        },
        {
          "code": "300003",
          "market": "SZ",
          "name": "乐普医疗",
          "weight": 0.7
        },
        {
          "code": "603309",
          "market": "SH",
          "name": "维力医疗",
          "weight": 0.4
        },
        {
          "code": "688198",
          "market": "SH",
          "name": "佰仁医疗",
          "weight": 0.5
        },
        {
          "code": "300326",
          "market": "SZ",
          "name": "凯利泰",
          "weight": 0.4
        },
        {
          "code": "600587",
          "market": "SH",
          "name": "新华医疗",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "cro-pharma",
      "name": "CRO/创新药",
      "nameEn": "CRO / Innovative Pharma",
      "usLeaders": [
        {
          "ticker": "LLY",
          "name": "Eli Lilly / 礼来",
          "weight": 1.0
        },
        {
          "ticker": "PFE",
          "name": "Pfizer / 辉瑞",
          "weight": 1.0
        },
        {
          "ticker": "BIIB",
          "name": "Biogen / 渤健",
          "weight": 0.8
        },
        {
          "ticker": "VRTX",
          "name": "Vertex / 福泰制药",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "603259",
          "market": "SH",
          "name": "药明康德",
          "weight": 1.0
        },
        {
          "code": "300759",
          "market": "SZ",
          "name": "康龙化成",
          "weight": 0.9
        },
        {
          "code": "300347",
          "market": "SZ",
          "name": "泰格医药",
          "weight": 0.9
        },
        {
          "code": "600276",
          "market": "SH",
          "name": "恒瑞医药",
          "weight": 1.0
        },
        {
          "code": "002821",
          "market": "SZ",
          "name": "凯莱英",
          "weight": 0.8
        },
        {
          "code": "603127",
          "market": "SH",
          "name": "昭衍新药",
          "weight": 0.6
        },
        {
          "code": "688202",
          "market": "SH",
          "name": "美迪西",
          "weight": 0.5
        },
        {
          "code": "688076",
          "market": "SH",
          "name": "诺泰生物",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "fintech",
      "name": "金融科技/支付",
      "nameEn": "FinTech / Payments",
      "usLeaders": [
        {
          "ticker": "V",
          "name": "Visa / Visa",
          "weight": 1.0
        },
        {
          "ticker": "MA",
          "name": "Mastercard / 万事达",
          "weight": 1.0
        },
        {
          "ticker": "PYPL",
          "name": "PayPal / PayPal",
          "weight": 0.8
        },
        {
          "ticker": "COIN",
          "name": "Coinbase / Coinbase",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "300059",
          "market": "SZ",
          "name": "东方财富",
          "weight": 1.0
        },
        {
          "code": "300033",
          "market": "SZ",
          "name": "同花顺",
          "weight": 1.0
        },
        {
          "code": "600570",
          "market": "SH",
          "name": "恒生电子",
          "weight": 0.9
        },
        {
          "code": "300348",
          "market": "SZ",
          "name": "长亮科技",
          "weight": 0.5
        },
        {
          "code": "600446",
          "market": "SH",
          "name": "金证股份",
          "weight": 0.7
        },
        {
          "code": "300377",
          "market": "SZ",
          "name": "赢时胜",
          "weight": 0.4
        },
        {
          "code": "600536",
          "market": "SH",
          "name": "中国软件",
          "weight": 0.5
        },
        {
          "code": "000948",
          "market": "SZ",
          "name": "南天信息",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "bank",
      "name": "银行",
      "nameEn": "Banking",
      "usLeaders": [
        {
          "ticker": "JPM",
          "name": "JPMorgan / 摩根大通",
          "weight": 1.0
        },
        {
          "ticker": "BAC",
          "name": "Bank of America / 美国银行",
          "weight": 1.0
        },
        {
          "ticker": "WFC",
          "name": "Wells Fargo / 富国银行",
          "weight": 0.8
        },
        {
          "ticker": "C",
          "name": "Citigroup / 花旗集团",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "600036",
          "market": "SH",
          "name": "招商银行",
          "weight": 1.0
        },
        {
          "code": "002142",
          "market": "SZ",
          "name": "宁波银行",
          "weight": 0.9
        },
        {
          "code": "000001",
          "market": "SZ",
          "name": "平安银行",
          "weight": 0.8
        },
        {
          "code": "601166",
          "market": "SH",
          "name": "兴业银行",
          "weight": 0.8
        },
        {
          "code": "601398",
          "market": "SH",
          "name": "工商银行",
          "weight": 0.7
        },
        {
          "code": "601288",
          "market": "SH",
          "name": "农业银行",
          "weight": 0.7
        }
      ]
    },
    {
      "id": "insurance",
      "name": "保险",
      "nameEn": "Insurance",
      "usLeaders": [
        {
          "ticker": "BRK-B",
          "name": "Berkshire Hathaway / 伯克希尔",
          "weight": 1.0
        },
        {
          "ticker": "AIG",
          "name": "AIG / 美国国际集团",
          "weight": 0.7
        },
        {
          "ticker": "PGR",
          "name": "Progressive / 前进保险",
          "weight": 0.8
        },
        {
          "ticker": "ALL",
          "name": "Allstate / 好事达",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "601318",
          "market": "SH",
          "name": "中国平安",
          "weight": 1.0
        },
        {
          "code": "601601",
          "market": "SH",
          "name": "中国太保",
          "weight": 0.9
        },
        {
          "code": "601628",
          "market": "SH",
          "name": "中国人寿",
          "weight": 0.9
        },
        {
          "code": "601336",
          "market": "SH",
          "name": "新华保险",
          "weight": 0.7
        },
        {
          "code": "601319",
          "market": "SH",
          "name": "中国人保",
          "weight": 0.6
        },
        {
          "code": "600291",
          "market": "SH",
          "name": "天茂集团",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "securities",
      "name": "券商",
      "nameEn": "Securities",
      "usLeaders": [
        {
          "ticker": "GS",
          "name": "Goldman Sachs / 高盛",
          "weight": 1.0
        },
        {
          "ticker": "MS",
          "name": "Morgan Stanley / 摩根士丹利",
          "weight": 1.0
        },
        {
          "ticker": "SCHW",
          "name": "Charles Schwab / 嘉信理财",
          "weight": 0.8
        },
        {
          "ticker": "CME",
          "name": "CME Group / CME集团",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "600030",
          "market": "SH",
          "name": "中信证券",
          "weight": 1.0
        },
        {
          "code": "601688",
          "market": "SH",
          "name": "华泰证券",
          "weight": 0.9
        },
        {
          "code": "601211",
          "market": "SH",
          "name": "国泰君安",
          "weight": 0.8
        },
        {
          "code": "300059",
          "market": "SZ",
          "name": "东方财富",
          "weight": 1.0
        },
        {
          "code": "600999",
          "market": "SH",
          "name": "招商证券",
          "weight": 0.7
        },
        {
          "code": "000776",
          "market": "SZ",
          "name": "广发证券",
          "weight": 0.7
        }
      ]
    },
    {
      "id": "baijiu",
      "name": "白酒",
      "nameEn": "Baijiu / Liquor",
      "usLeaders": [
        {
          "ticker": "DEO",
          "name": "Diageo / 帝亚吉欧",
          "weight": 1.0
        },
        {
          "ticker": "STZ",
          "name": "Constellation Brands / 星座品牌",
          "weight": 0.8
        },
        {
          "ticker": "BF-B",
          "name": "Brown-Forman / 百富门",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "600519",
          "market": "SH",
          "name": "贵州茅台",
          "weight": 1.0
        },
        {
          "code": "000858",
          "market": "SZ",
          "name": "五粮液",
          "weight": 1.0
        },
        {
          "code": "000568",
          "market": "SZ",
          "name": "泸州老窖",
          "weight": 0.9
        },
        {
          "code": "600809",
          "market": "SH",
          "name": "山西汾酒",
          "weight": 0.9
        },
        {
          "code": "002304",
          "market": "SZ",
          "name": "洋河股份",
          "weight": 0.8
        },
        {
          "code": "000596",
          "market": "SZ",
          "name": "古井贡酒",
          "weight": 0.7
        }
      ]
    },
    {
      "id": "food-beverage",
      "name": "食品饮料",
      "nameEn": "Food & Beverage",
      "usLeaders": [
        {
          "ticker": "KO",
          "name": "Coca-Cola / 可口可乐",
          "weight": 1.0
        },
        {
          "ticker": "PEP",
          "name": "PepsiCo / 百事",
          "weight": 1.0
        },
        {
          "ticker": "MDLZ",
          "name": "Mondelez / 亿滋",
          "weight": 0.8
        },
        {
          "ticker": "GIS",
          "name": "General Mills / 通用磨坊",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "603288",
          "market": "SH",
          "name": "海天味业",
          "weight": 1.0
        },
        {
          "code": "600887",
          "market": "SH",
          "name": "伊利股份",
          "weight": 1.0
        },
        {
          "code": "000895",
          "market": "SZ",
          "name": "双汇发展",
          "weight": 0.8
        },
        {
          "code": "002507",
          "market": "SZ",
          "name": "涪陵榨菜",
          "weight": 0.7
        },
        {
          "code": "603345",
          "market": "SH",
          "name": "安井食品",
          "weight": 0.7
        },
        {
          "code": "300999",
          "market": "SZ",
          "name": "金龙鱼",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "home-appliances",
      "name": "家电",
      "nameEn": "Home Appliances",
      "usLeaders": [
        {
          "ticker": "WHR",
          "name": "Whirlpool / 惠而浦",
          "weight": 1.0
        },
        {
          "ticker": "EL",
          "name": "Estee Lauder / 雅诗兰黛",
          "weight": 0.7
        },
        {
          "ticker": "GE",
          "name": "GE Aerospace / 通用电气",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "000333",
          "market": "SZ",
          "name": "美的集团",
          "weight": 1.0
        },
        {
          "code": "000651",
          "market": "SZ",
          "name": "格力电器",
          "weight": 1.0
        },
        {
          "code": "600690",
          "market": "SH",
          "name": "海尔智家",
          "weight": 1.0
        },
        {
          "code": "002032",
          "market": "SZ",
          "name": "苏泊尔",
          "weight": 0.7
        },
        {
          "code": "002508",
          "market": "SZ",
          "name": "老板电器",
          "weight": 0.6
        },
        {
          "code": "603195",
          "market": "SH",
          "name": "欧普照明",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "textile-apparel",
      "name": "纺织服装",
      "nameEn": "Textile & Apparel",
      "usLeaders": [
        {
          "ticker": "LULU",
          "name": "Lululemon / 露露乐蒙",
          "weight": 1.0
        },
        {
          "ticker": "NKE",
          "name": "Nike / 耐克",
          "weight": 1.0
        },
        {
          "ticker": "VFC",
          "name": "VF Corp / VF集团",
          "weight": 0.7
        },
        {
          "ticker": "DECK",
          "name": "Deckers / 德克斯户外",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "600398",
          "market": "SH",
          "name": "海澜之家",
          "weight": 1.0
        },
        {
          "code": "002563",
          "market": "SZ",
          "name": "森马服饰",
          "weight": 0.8
        },
        {
          "code": "603877",
          "market": "SH",
          "name": "太平鸟",
          "weight": 0.6
        },
        {
          "code": "002832",
          "market": "SZ",
          "name": "比音勒芬",
          "weight": 0.6
        },
        {
          "code": "600177",
          "market": "SH",
          "name": "雅戈尔",
          "weight": 0.5
        },
        {
          "code": "002293",
          "market": "SZ",
          "name": "罗莱生活",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "auto-whole",
      "name": "汽车整车",
      "nameEn": "Auto OEM",
      "usLeaders": [
        {
          "ticker": "TSLA",
          "name": "Tesla / 特斯拉",
          "weight": 1.0
        },
        {
          "ticker": "GM",
          "name": "General Motors / 通用汽车",
          "weight": 0.8
        },
        {
          "ticker": "F",
          "name": "Ford / 福特",
          "weight": 0.7
        },
        {
          "ticker": "RIVN",
          "name": "Rivian / Rivian",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "002594",
          "market": "SZ",
          "name": "比亚迪",
          "weight": 1.0
        },
        {
          "code": "601633",
          "market": "SH",
          "name": "长城汽车",
          "weight": 0.8
        },
        {
          "code": "000625",
          "market": "SZ",
          "name": "长安汽车",
          "weight": 0.8
        },
        {
          "code": "600104",
          "market": "SH",
          "name": "上汽集团",
          "weight": 0.7
        },
        {
          "code": "601238",
          "market": "SH",
          "name": "广汽集团",
          "weight": 0.6
        },
        {
          "code": "601127",
          "market": "SH",
          "name": "赛力斯",
          "weight": 0.7
        }
      ]
    },
    {
      "id": "auto-parts",
      "name": "汽车零部件",
      "nameEn": "Auto Parts",
      "usLeaders": [
        {
          "ticker": "APTV",
          "name": "Aptiv / 安波福",
          "weight": 1.0
        },
        {
          "ticker": "BWA",
          "name": "BorgWarner / 博格华纳",
          "weight": 0.8
        },
        {
          "ticker": "ALV",
          "name": "Autoliv / 奥托立夫",
          "weight": 0.7
        },
        {
          "ticker": "LEA",
          "name": "Lear / 李尔",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "300750",
          "market": "SZ",
          "name": "宁德时代",
          "weight": 1.0
        },
        {
          "code": "002050",
          "market": "SZ",
          "name": "三花智控",
          "weight": 1.0
        },
        {
          "code": "601689",
          "market": "SH",
          "name": "拓普集团",
          "weight": 0.9
        },
        {
          "code": "603596",
          "market": "SH",
          "name": "伯特利",
          "weight": 0.8
        },
        {
          "code": "603197",
          "market": "SH",
          "name": "保隆科技",
          "weight": 0.6
        },
        {
          "code": "600699",
          "market": "SH",
          "name": "均胜电子",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "chemicals",
      "name": "化工/新材料",
      "nameEn": "Chemicals / New Materials",
      "usLeaders": [
        {
          "ticker": "DD",
          "name": "DuPont / 杜邦",
          "weight": 1.0
        },
        {
          "ticker": "DOW",
          "name": "Dow / 陶氏",
          "weight": 1.0
        },
        {
          "ticker": "ALB",
          "name": "Albemarle / 雅宝",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "600309",
          "market": "SH",
          "name": "万华化学",
          "weight": 1.0
        },
        {
          "code": "600426",
          "market": "SH",
          "name": "华鲁恒升",
          "weight": 0.9
        },
        {
          "code": "002001",
          "market": "SZ",
          "name": "新和成",
          "weight": 0.8
        },
        {
          "code": "002812",
          "market": "SZ",
          "name": "恩捷股份",
          "weight": 0.7
        },
        {
          "code": "002709",
          "market": "SZ",
          "name": "天赐材料",
          "weight": 0.7
        },
        {
          "code": "603799",
          "market": "SH",
          "name": "华友钴业",
          "weight": 0.8
        },
        {
          "code": "600352",
          "market": "SH",
          "name": "浙江龙盛",
          "weight": 0.6
        },
        {
          "code": "600160",
          "market": "SH",
          "name": "巨化股份",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "steel",
      "name": "钢铁",
      "nameEn": "Steel",
      "usLeaders": [
        {
          "ticker": "X",
          "name": "US Steel / 美国钢铁",
          "weight": 1.0
        },
        {
          "ticker": "NUE",
          "name": "Nucor / 纽柯",
          "weight": 1.0
        },
        {
          "ticker": "STLD",
          "name": "Steel Dynamics / 钢铁动力",
          "weight": 0.8
        },
        {
          "ticker": "MT",
          "name": "ArcelorMittal / 安赛乐米塔尔",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "600019",
          "market": "SH",
          "name": "宝钢股份",
          "weight": 1.0
        },
        {
          "code": "000898",
          "market": "SZ",
          "name": "鞍钢股份",
          "weight": 0.8
        },
        {
          "code": "000959",
          "market": "SZ",
          "name": "首钢股份",
          "weight": 0.7
        },
        {
          "code": "600010",
          "market": "SH",
          "name": "包钢股份",
          "weight": 0.5
        },
        {
          "code": "600808",
          "market": "SH",
          "name": "马钢股份",
          "weight": 0.5
        },
        {
          "code": "600022",
          "market": "SH",
          "name": "山东钢铁",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "coal",
      "name": "煤炭",
      "nameEn": "Coal",
      "usLeaders": [
        {
          "ticker": "BTU",
          "name": "Peabody Energy / 皮博迪能源",
          "weight": 1.0
        },
        {
          "ticker": "ARCH",
          "name": "Arch Resources / Arch资源",
          "weight": 0.8
        },
        {
          "ticker": "CEIX",
          "name": "CONSOL Energy / CONSOL能源",
          "weight": 0.7
        },
        {
          "ticker": "ARLP",
          "name": "Alliance Resource / Alliance资源",
          "weight": 0.6
        }
      ],
      "aTargets": [
        {
          "code": "601088",
          "market": "SH",
          "name": "中国神华",
          "weight": 1.0
        },
        {
          "code": "601225",
          "market": "SH",
          "name": "陕西煤业",
          "weight": 1.0
        },
        {
          "code": "600188",
          "market": "SH",
          "name": "兖矿能源",
          "weight": 0.8
        },
        {
          "code": "601898",
          "market": "SH",
          "name": "中煤能源",
          "weight": 0.7
        },
        {
          "code": "601699",
          "market": "SH",
          "name": "潞安环能",
          "weight": 0.6
        },
        {
          "code": "600971",
          "market": "SH",
          "name": "恒源煤电",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "oil-petrochemical",
      "name": "石油/石化",
      "nameEn": "Oil / Petrochemical",
      "usLeaders": [
        {
          "ticker": "XOM",
          "name": "Exxon Mobil / 埃克森美孚",
          "weight": 1.0
        },
        {
          "ticker": "CVX",
          "name": "Chevron / 雪佛龙",
          "weight": 1.0
        },
        {
          "ticker": "COP",
          "name": "ConocoPhillips / 康菲石油",
          "weight": 0.8
        },
        {
          "ticker": "MPC",
          "name": "Marathon Petroleum / 马拉松石油",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "601857",
          "market": "SH",
          "name": "中国石油",
          "weight": 1.0
        },
        {
          "code": "600028",
          "market": "SH",
          "name": "中国石化",
          "weight": 1.0
        },
        {
          "code": "600938",
          "market": "SH",
          "name": "中国海油",
          "weight": 1.0
        },
        {
          "code": "002493",
          "market": "SZ",
          "name": "荣盛石化",
          "weight": 0.7
        },
        {
          "code": "600346",
          "market": "SH",
          "name": "恒力石化",
          "weight": 0.7
        },
        {
          "code": "000703",
          "market": "SZ",
          "name": "恒逸石化",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "building-materials",
      "name": "建材",
      "nameEn": "Building Materials",
      "usLeaders": [
        {
          "ticker": "HD",
          "name": "Home Depot / 家得宝",
          "weight": 1.0
        },
        {
          "ticker": "LOW",
          "name": "Lowe's / 劳氏",
          "weight": 0.9
        },
        {
          "ticker": "SHW",
          "name": "Sherwin-Williams / 宣伟",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "600585",
          "market": "SH",
          "name": "海螺水泥",
          "weight": 1.0
        },
        {
          "code": "002271",
          "market": "SZ",
          "name": "东方雨虹",
          "weight": 0.9
        },
        {
          "code": "000786",
          "market": "SZ",
          "name": "北新建材",
          "weight": 0.8
        },
        {
          "code": "600801",
          "market": "SH",
          "name": "华新水泥",
          "weight": 0.6
        },
        {
          "code": "002372",
          "market": "SZ",
          "name": "伟星新材",
          "weight": 0.6
        },
        {
          "code": "603737",
          "market": "SH",
          "name": "三棵树",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "construction",
      "name": "建筑",
      "nameEn": "Construction",
      "usLeaders": [
        {
          "ticker": "FLR",
          "name": "Fluor / 福陆",
          "weight": 1.0
        },
        {
          "ticker": "J",
          "name": "Jacobs / 雅各布工程",
          "weight": 0.8
        },
        {
          "ticker": "ACM",
          "name": "AECOM / AECOM",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "601668",
          "market": "SH",
          "name": "中国建筑",
          "weight": 1.0
        },
        {
          "code": "601390",
          "market": "SH",
          "name": "中国中铁",
          "weight": 1.0
        },
        {
          "code": "601186",
          "market": "SH",
          "name": "中国铁建",
          "weight": 0.9
        },
        {
          "code": "601618",
          "market": "SH",
          "name": "中国中冶",
          "weight": 0.7
        },
        {
          "code": "601669",
          "market": "SH",
          "name": "中国电建",
          "weight": 0.7
        },
        {
          "code": "601117",
          "market": "SH",
          "name": "中国化学",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "logistics",
      "name": "物流快递",
      "nameEn": "Logistics / Express",
      "usLeaders": [
        {
          "ticker": "FDX",
          "name": "FedEx / 联邦快递",
          "weight": 1.0
        },
        {
          "ticker": "UPS",
          "name": "UPS / 联合包裹",
          "weight": 1.0
        },
        {
          "ticker": "XPO",
          "name": "XPO / XPO物流",
          "weight": 0.6
        },
        {
          "ticker": "ZTO",
          "name": "ZTO Express / 中通快递",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "002352",
          "market": "SZ",
          "name": "顺丰控股",
          "weight": 1.0
        },
        {
          "code": "002120",
          "market": "SZ",
          "name": "韵达股份",
          "weight": 0.8
        },
        {
          "code": "600233",
          "market": "SH",
          "name": "圆通速递",
          "weight": 0.8
        },
        {
          "code": "603056",
          "market": "SH",
          "name": "德邦股份",
          "weight": 0.6
        },
        {
          "code": "002468",
          "market": "SZ",
          "name": "申通快递",
          "weight": 0.5
        },
        {
          "code": "603871",
          "market": "SH",
          "name": "嘉友国际",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "defense-aerospace",
      "name": "军工/航空航天",
      "nameEn": "Defense / Aerospace",
      "usLeaders": [
        {
          "ticker": "LMT",
          "name": "Lockheed Martin / 洛克希德·马丁",
          "weight": 1.0
        },
        {
          "ticker": "NOC",
          "name": "Northrop Grumman / 诺斯洛普·格鲁曼",
          "weight": 1.0
        },
        {
          "ticker": "RTX",
          "name": "RTX / RTX",
          "weight": 1.0
        },
        {
          "ticker": "BA",
          "name": "Boeing / 波音",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "600760",
          "market": "SH",
          "name": "中航沈飞",
          "weight": 1.0
        },
        {
          "code": "600893",
          "market": "SH",
          "name": "航发动力",
          "weight": 1.0
        },
        {
          "code": "000768",
          "market": "SZ",
          "name": "中航西飞",
          "weight": 0.9
        },
        {
          "code": "600038",
          "market": "SH",
          "name": "中直股份",
          "weight": 0.7
        },
        {
          "code": "002179",
          "market": "SZ",
          "name": "中航光电",
          "weight": 0.8
        },
        {
          "code": "600391",
          "market": "SH",
          "name": "航发科技",
          "weight": 0.5
        },
        {
          "code": "600862",
          "market": "SH",
          "name": "中航高科",
          "weight": 0.5
        },
        {
          "code": "600765",
          "market": "SH",
          "name": "中航重机",
          "weight": 0.6
        }
      ]
    },
    {
      "id": "gaming-media",
      "name": "传媒/游戏",
      "nameEn": "Gaming / Media",
      "usLeaders": [
        {
          "ticker": "EA",
          "name": "Electronic Arts / 艺电",
          "weight": 1.0
        },
        {
          "ticker": "TTWO",
          "name": "Take-Two / Take-Two",
          "weight": 0.9
        },
        {
          "ticker": "NFLX",
          "name": "Netflix / 奈飞",
          "weight": 1.0
        },
        {
          "ticker": "DIS",
          "name": "Disney / 迪士尼",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "002555",
          "market": "SZ",
          "name": "三七互娱",
          "weight": 1.0
        },
        {
          "code": "002624",
          "market": "SZ",
          "name": "完美世界",
          "weight": 0.9
        },
        {
          "code": "603444",
          "market": "SH",
          "name": "吉比特",
          "weight": 0.9
        },
        {
          "code": "300418",
          "market": "SZ",
          "name": "昆仑万维",
          "weight": 0.8
        },
        {
          "code": "002517",
          "market": "SZ",
          "name": "恺英网络",
          "weight": 0.7
        },
        {
          "code": "002602",
          "market": "SZ",
          "name": "世纪华通",
          "weight": 0.7
        },
        {
          "code": "300031",
          "market": "SZ",
          "name": "宝通科技",
          "weight": 0.5
        },
        {
          "code": "600373",
          "market": "SH",
          "name": "中文传媒",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "aviation",
      "name": "航空",
      "nameEn": "Aviation",
      "usLeaders": [
        {
          "ticker": "AAL",
          "name": "American Airlines / 美国航空",
          "weight": 1.0
        },
        {
          "ticker": "DAL",
          "name": "Delta Air / 达美航空",
          "weight": 1.0
        },
        {
          "ticker": "UAL",
          "name": "United Airlines / 美联航",
          "weight": 0.9
        },
        {
          "ticker": "LUV",
          "name": "Southwest / 西南航空",
          "weight": 0.7
        }
      ],
      "aTargets": [
        {
          "code": "601111",
          "market": "SH",
          "name": "中国国航",
          "weight": 1.0
        },
        {
          "code": "600029",
          "market": "SH",
          "name": "南方航空",
          "weight": 1.0
        },
        {
          "code": "600115",
          "market": "SH",
          "name": "东方航空",
          "weight": 0.9
        },
        {
          "code": "601021",
          "market": "SH",
          "name": "春秋航空",
          "weight": 0.7
        },
        {
          "code": "603885",
          "market": "SH",
          "name": "吉祥航空",
          "weight": 0.6
        },
        {
          "code": "600221",
          "market": "SH",
          "name": "海航控股",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "e-commerce",
      "name": "电商/互联网",
      "nameEn": "E-commerce / Internet",
      "usLeaders": [
        {
          "ticker": "BABA",
          "name": "Alibaba / 阿里巴巴",
          "weight": 1.0
        },
        {
          "ticker": "JD",
          "name": "JD.com / 京东",
          "weight": 1.0
        },
        {
          "ticker": "PDD",
          "name": "PDD Holdings / 拼多多",
          "weight": 1.0
        },
        {
          "ticker": "MELI",
          "name": "MercadoLibre / MercadoLibre",
          "weight": 0.8
        }
      ],
      "aTargets": [
        {
          "code": "300792",
          "market": "SZ",
          "name": "壹网壹创",
          "weight": 1.0
        },
        {
          "code": "300785",
          "market": "SZ",
          "name": "值得买",
          "weight": 0.8
        },
        {
          "code": "603613",
          "market": "SH",
          "name": "国联股份",
          "weight": 0.8
        },
        {
          "code": "002024",
          "market": "SZ",
          "name": "苏宁易购",
          "weight": 0.6
        },
        {
          "code": "301078",
          "market": "SZ",
          "name": "孩子王",
          "weight": 0.5
        },
        {
          "code": "003010",
          "market": "SZ",
          "name": "若羽臣",
          "weight": 0.5
        }
      ]
    },
    {
      "id": "education",
      "name": "教育",
      "nameEn": "Education",
      "usLeaders": [
        {
          "ticker": "EDU",
          "name": "New Oriental / 新东方",
          "weight": 1.0
        },
        {
          "ticker": "TAL",
          "name": "TAL Education / 好未来",
          "weight": 1.0
        },
        {
          "ticker": "LOPE",
          "name": "Grand Canyon / 大峡谷教育",
          "weight": 0.6
        },
        {
          "ticker": "LRN",
          "name": "Stride / Stride",
          "weight": 0.5
        }
      ],
      "aTargets": [
        {
          "code": "002607",
          "market": "SZ",
          "name": "中公教育",
          "weight": 1.0
        },
        {
          "code": "002659",
          "market": "SZ",
          "name": "凯文教育",
          "weight": 0.7
        },
        {
          "code": "000526",
          "market": "SZ",
          "name": "学大教育",
          "weight": 0.7
        },
        {
          "code": "600661",
          "market": "SH",
          "name": "昂立教育",
          "weight": 0.6
        },
        {
          "code": "300192",
          "market": "SZ",
          "name": "科德教育",
          "weight": 0.5
        },
        {
          "code": "600730",
          "market": "SH",
          "name": "中国高科",
          "weight": 0.4
        }
      ]
    },
    {
      "id": "telecom",
      "name": "通信运营商",
      "nameEn": "Telecom Operators",
      "usLeaders": [
        {
          "ticker": "T",
          "name": "AT&T / AT&T",
          "weight": 1.0
        },
        {
          "ticker": "VZ",
          "name": "Verizon / 威瑞森",
          "weight": 1.0
        },
        {
          "ticker": "TMUS",
          "name": "T-Mobile / T-Mobile",
          "weight": 0.9
        }
      ],
      "aTargets": [
        {
          "code": "600941",
          "market": "SH",
          "name": "中国移动",
          "weight": 1.0
        },
        {
          "code": "601728",
          "market": "SH",
          "name": "中国电信",
          "weight": 0.9
        },
        {
          "code": "600050",
          "market": "SH",
          "name": "中国联通",
          "weight": 0.8
        },
        {
          "code": "600487",
          "market": "SH",
          "name": "亨通光电",
          "weight": 0.6
        },
        {
          "code": "600498",
          "market": "SH",
          "name": "烽火通信",
          "weight": 0.6
        },
        {
          "code": "000063",
          "market": "SZ",
          "name": "中兴通讯",
          "weight": 0.7
        }
      ]
    },
    {
      "id": "index-mapping",
      "name": "指数映射",
      "nameEn": "Index Mapping",
      "type": "index",
      "usLeaders": [
        {
          "ticker": "SOX",
          "name": "费城半导体指数",
          "weight": 1.0
        },
        {
          "ticker": "NDX",
          "name": "纳斯达克100",
          "weight": 1.0
        },
        {
          "ticker": "SPX",
          "name": "标普500",
          "weight": 1.0
        },
        {
          "ticker": "DJI",
          "name": "道琼斯工业指数",
          "weight": 1.0
        }
      ],
      "aTargets": [
        {
          "code": "H30091",
          "market": "SZ",
          "name": "半导体指数",
          "weight": 1.0
        },
        {
          "code": "399006",
          "market": "SZ",
          "name": "创业板指",
          "weight": 1.0
        },
        {
          "code": "000300",
          "market": "SH",
          "name": "沪深300",
          "weight": 1.0
        },
        {
          "code": "000001",
          "market": "SH",
          "name": "上证指数",
          "weight": 1.0
        }
      ]
    }
  ]
}
