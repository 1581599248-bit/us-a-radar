#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股-A股映射雷达站 v3 — 移动端优先 + 截图导出
"""
import json, os, time, re, threading, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template_string

APP_DIR = Path(__file__).parent
REFRESH_INTERVAL = 300
API_TIMEOUT = 12

with open(APP_DIR / "mapping.json", "r", encoding="utf-8") as f:
    MAPPING = json.load(f)

class Fetcher:
    def __init__(self):
        self.cache = {"us": {}, "a": {}, "sectors": [], "updatedAt": None, "marketStatus": {}}
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
            time.sleep(0.1)
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

    def refresh(self):
        print(f"[{datetime.now()}] 开始刷新...")
        all_us = set()
        all_a = []
        for s in MAPPING["sectors"]:
            for u in s["usLeaders"]:
                all_us.add(u["ticker"])
            for a in s["aTargets"]:
                all_a.append(a)
        seen = set()
        uniq_a = [s for s in all_a if not (s["code"] in seen or seen.add(s["code"]))]
        us_data = self._fetch_us(list(all_us))
        a_data = self._fetch_a(uniq_a)
        sectors = []
        for sector in MAPPING["sectors"]:
            tw = sum(u.get("weight", 1) for u in sector["usLeaders"])
            wp = 0.0
            usl = []
            for u in sector["usLeaders"]:
                info = us_data.get(u["ticker"], {})
                p = info.get("changePercent")
                if p is not None:
                    wp += p * u.get("weight", 1)
                usl.append({"ticker": u["ticker"], "name": u["name"], "weight": u.get("weight", 1), **info})
            wp = wp / tw if tw else 0
            at = []
            for a in sector["aTargets"]:
                info = a_data.get(a["code"], {})
                at.append({"code": a["code"], "name": a["name"], "market": a["market"], "weight": a.get("weight", 1), **info})
            g, gt, gc = "neutral", "中性", "gray"
            if wp is None:
                g, gt, gc = "unknown", "暂无", "gray"
            elif wp >= 5:
                g, gt, gc = "strong_positive", "强利好", "red"
            elif wp >= 2:
                g, gt, gc = "positive", "利好", "red"
            elif wp > -2:
                g, gt, gc = "neutral", "中性", "gray"
            elif wp > -5:
                g, gt, gc = "negative", "利空", "green"
            else:
                g, gt, gc = "strong_negative", "强利空", "green"
            sectors.append({"id": sector["id"], "name": sector["name"], "nameEn": sector["nameEn"], "usWeightedPct": round(wp, 2), "usLeaders": usl, "aTargets": at, "guidance": {"level": g, "text": gt, "color": gc}})
        sectors.sort(key=lambda x: abs(x.get("usWeightedPct", 0)), reverse=True)
        now = datetime.now(timezone(timedelta(hours=8)))
        t = now.time()
        wd = now.weekday()
        us_open = t.hour >= 21 or t.hour <= 5
        a_open = wd < 5 and ((9 <= t.hour <= 10) or (t.hour == 11 and t.minute <= 30) or (13 <= t.hour <= 14) or (t.hour == 15 and t.minute == 0))
        a_label = "A股交易中" if a_open else ("A股未开盘" if wd < 5 and t.hour < 9 else "A股已收盘")
        ms = {"time": now.strftime("%Y-%m-%d %H:%M"), "us": "美股交易中" if us_open else "美股已收盘", "a": a_label, "isUSOpen": us_open, "isAOpen": a_open}
        with self.lock:
            self.cache = {"us": us_data, "a": a_data, "sectors": sectors, "updatedAt": now.isoformat(), "marketStatus": ms}
        print(f"[{datetime.now()}] 刷新完成 US={len(us_data)} A={len(a_data)}")

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

HTML_PAGE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="format-detection" content="telephone=no">
<meta name="theme-color" content="#1a1a2e">
<title>美股-A股映射雷达</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { font-size:14px; }
body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; background:#0d0d1a; color:#fff; min-height:100vh; overflow-x:hidden; }
.up { color:#ff6b6b; } .down { color:#4ecdc4; }
.up-bg { background:rgba(255,107,107,0.12); } .down-bg { background:rgba(78,205,196,0.12); }
.neu-bg { background:rgba(148,163,184,0.1); }
.badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:700; }
.card { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08); border-radius:12px; overflow:hidden; margin-bottom:10px; }
.sector-row { display:flex; border-bottom:1px solid rgba(255,255,255,0.05); padding:8px 12px; align-items:center; }
.sector-row:last-child { border-bottom:none; }
.ticker { font-size:11px; color:#64748b; }
.btn { display:inline-flex; align-items:center; justify-content:center; padding:8px 16px; border-radius:8px; font-size:12px; font-weight:600; border:none; cursor:pointer; }
.btn-primary { background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:#fff; }
.btn-secondary { background:rgba(255,255,255,0.1); color:#fff; }
#overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.85); z-index:9999; justify-content:center; align-items:center; flex-direction:column; padding:20px; }
#preview { width:100%; max-width:400px; border-radius:12px; overflow:auto; }
@media print { body { background:#fff; color:#000; } .btn { display:none; } .card { border:1px solid #ddd; } }
::-webkit-scrollbar { width:2px; } ::-webkit-scrollbar-track { background:transparent; } ::-webkit-scrollbar-thumb { background:rgba(255,255,255,0.2); border-radius:2px; }
</style>
</head>
<body>
<header style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%); border-bottom:1px solid rgba(255,255,255,0.06);" class="px-4 py-3 sticky top-0 z-50">
  <div class="flex justify-between items-center">
    <div class="flex items-center gap-2">
      <div class="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white font-bold text-lg">🌐</div>
      <div>
        <h1 class="font-bold text-white text-base">美股-A股映射雷达</h1>
        <p class="text-xs" style="color:#64748b;">美股当夜涨跌 → A股次日指引</p>
      </div>
    </div>
    <div id="marketStatus" class="text-xs" style="color:#64748b;"></div>
  </div>
</header>
<div class="px-4 pt-3"><div id="summary" class="flex gap-2 mb-3"></div></div>
<div class="px-4 pb-3 flex gap-2">
  <input id="searchInput" type="text" placeholder="搜索板块..." class="flex-1 px-3 py-2 rounded-lg text-xs focus:outline-none" style="background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.08); color:#fff;">
  <select id="filterSelect" class="px-3 py-2 rounded-lg text-xs focus:outline-none" style="background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.08); color:#fff;">
    <option value="all" style="background:#1a1a2e;">全部</option>
    <option value="strong_positive" style="background:#1a1a2e;">强利好</option>
    <option value="positive" style="background:#1a1a2e;">利好</option>
    <option value="neutral" style="background:#1a1a2e;">中性</option>
    <option value="negative" style="background:#1a1a2e;">利空</option>
    <option value="strong_negative" style="background:#1a1a2e;">强利空</option>
  </select>
</div>
<div class="px-4 pb-3 flex gap-2">
  <button class="btn btn-primary flex-1" onclick="takeScreenshot()">📸 截图发给客户</button>
  <button class="btn btn-secondary" style="flex:0.5;" onclick="window.print()">🖨️ 打印</button>
</div>
<main class="px-4 pb-20"><div id="sectorsGrid"></div></main>
<div id="overlay"><div id="preview"></div></div>
<script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>
<script>
let DATA = null;
const fmt = n => n == null ? '—' : (n > 0 ? '+' : '') + n.toFixed(2);
const pctCls = n => n == null ? 'text-gray-500' : n > 0 ? 'up' : n < 0 ? 'down' : 'text-gray-400';
const badgeCls = l => ({strong_positive:'up-bg up',positive:'up-bg up',neutral:'neu-bg text-gray-400',negative:'down-bg down',strong_negative:'down-bg down',unknown:'bg-gray-800 text-gray-500'}[l]||'bg-gray-800 text-gray-500');
async function load() { try { const r = await fetch('/api/data'); DATA = await r.json(); render(DATA); } catch(e) { console.error('加载失败:', e); } }
function render(data) {
  if(!data) return;
  const m = data.marketStatus || {};
  document.getElementById('marketStatus').innerHTML = `<span class="inline-flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full" style="background:${m.isUSOpen?'#4ecdc4':'#64748b'}"></span>${m.us}</span><span class="mx-1">·</span><span class="inline-flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full" style="background:${m.isAOpen?'#4ecdc4':'#64748b'}"></span>${m.a}</span><span class="mx-1">·</span><span class="text-gray-600">${m.time}</span>`;
  const s = data.sectors || [];
  const sp = s.filter(x=>x.guidance.level==='strong_positive').length;
  const p = s.filter(x=>x.guidance.level==='positive').length;
  const n = s.filter(x=>x.guidance.level==='neutral').length;
  const neg = s.filter(x=>x.guidance.level==='negative'||x.guidance.level==='strong_negative').length;
  document.getElementById('summary').innerHTML = [{l:'强利好',v:sp,c:'up-bg up'},{l:'利好',v:p,c:'up-bg up'},{l:'中性',v:n,c:'neu-bg text-gray-400'},{l:'利空',v:neg,c:'down-bg down'}].map(x=>`<div class="rounded-lg px-3 py-2 ${x.c} text-xs flex-1 text-center"><div class="font-bold text-lg">${x.v}</div><div class="text-xs opacity-70">${x.l}</div></div>`).join('');
  const q = (document.getElementById('searchInput').value||'').toLowerCase();
  const f = document.getElementById('filterSelect').value;
  const filtered = s.filter(sector=>{ const mq = !q || sector.name.toLowerCase().includes(q) || sector.nameEn.toLowerCase().includes(q) || sector.usLeaders.some(u=>u.name.toLowerCase().includes(q)||u.ticker.toLowerCase().includes(q)) || sector.aTargets.some(a=>a.name.toLowerCase().includes(q)||a.code.includes(q)); return mq && (f==='all' || sector.guidance.level===f); });
  document.getElementById('sectorsGrid').innerHTML = filtered.map(sector=>{
    const g = sector.guidance;
    const wp = sector.usWeightedPct;
    const wpStr = wp==null?'—':(wp>0?'+':'')+wp.toFixed(2)+'%';
    const usRows = sector.usLeaders.map(u=>`<div class="sector-row"><div class="flex-1"><span class="font-medium text-sm">${u.name}</span><div class="ticker">${u.ticker}</div></div><span class="font-mono font-bold text-sm ${pctCls(u.changePercent)}">${u.changePercent==null?'—':fmt(u.changePercent)+'%'}</span></div>`).join('');
    const aRows = sector.aTargets.map(a=>`<div class="sector-row"><div class="flex-1"><span class="font-medium text-sm">${a.name}</span><div class="ticker">${a.code}.${a.market}</div></div><span class="font-mono text-sm ${pctCls(a.changePercent)}">${a.changePercent==null?'—':fmt(a.changePercent)+'%'}</span></div>`).join('');
    return `<div class="card"><div class="px-3 py-3 flex items-center justify-between" style="border-bottom:1px solid rgba(255,255,255,0.06);"><div class="flex items-center gap-2"><span class="font-bold text-base text-white">${sector.name}</span><span class="badge ${badgeCls(g.level)}">${g.text}</span></div><div class="text-right"><span class="text-xl font-bold font-mono ${pctCls(wp)}">${wpStr}</span></div></div><div class="px-3 py-2 text-xs font-semibold tracking-wider" style="color:#64748b; background:rgba(255,255,255,0.02);">🇺🇸 美股龙头</div><div>${usRows}</div><div class="px-3 py-2 text-xs font-semibold tracking-wider" style="color:#64748b; background:rgba(255,255,255,0.02); border-top:1px solid rgba(255,255,255,0.05);">🇨🇳 A股映射</div><div>${aRows}</div></div>`;
  }).join('');
}
function takeScreenshot() {
  const overlay = document.getElementById('overlay');
  const preview = document.getElementById('preview');
  overlay.style.display = 'none';
  html2canvas(document.body, {scale:2, useCORS:true, logging:false, backgroundColor:'#0d0d1a'}).then(canvas=>{
    overlay.style.display = 'flex';
    const img = document.createElement('img');
    img.src = canvas.toDataURL('image/png');
    img.style.width = '100%';
    img.style.borderRadius = '12px';
    preview.innerHTML = '';
    preview.appendChild(img);
    const dl = document.createElement('a');
    dl.href = canvas.toDataURL('image/png');
    dl.download = '美股A股雷达_' + new Date().toISOString().slice(0,10) + '.png';
    dl.style = 'display:block;text-align:center;margin-top:16px;padding:10px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;border-radius:8px;text-decoration:none;font-weight:700;';
    dl.innerText = '⬇️ 下载图片';
    preview.appendChild(dl);
  });
  overlay.onclick = function(e){ if(e.target===overlay) overlay.style.display='none'; };
}
render(DATA);
load();
setInterval(load, 30000);
document.getElementById('searchInput').addEventListener('input', ()=>render(DATA));
document.getElementById('filterSelect').addEventListener('change', ()=>render(DATA));
</script>
</body>
</html>'''

if __name__ == "__main__":
    try:
        fetcher.refresh()
    except Exception as e:
        print(f"[WARN] 首次刷新: {e}")
    threading.Thread(target=_bg, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"[INFO] 启动 http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
