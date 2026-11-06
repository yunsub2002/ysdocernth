# -*- coding: utf-8 -*-
"""
VALUE HTS v2 — 매일 추천 스캐너
  python update_dashboard.py          : 4개 시장 전종목 스캔 → 카테고리별 추천 → VALUE_HTS.html 생성·열기
  python update_dashboard.py --live   : 위 작업 + 로컬 서버에서 60초마다 추천 종목 현재가 자동 갱신
  python update_dashboard.py --pred   : 전종목 선행 예측(pred.json)만 다시 계산
시장: NASDAQ · NYSE · KOSPI · KOSDAQ (Yahoo Finance 스크리너로 전종목 시세·52주 고저·PER·PBR·시총 수집)
카테고리: 오늘의 TOP20 / 저PER·저PBR 가치주 / 고점대비 급락 반등 / 52주 저점권 / 주도주 눌림목 / 동전주 10배 후보
※ 투자 권유가 아닌 기계적 스크리닝 결과입니다.
"""
import sys, os, json, time, math, subprocess, threading, webbrowser, datetime
BASE = os.path.dirname(os.path.abspath(__file__))
# index.html 하나가 템플릿 겸 결과물 (/*@BAKED*/ 구간만 매번 교체)
TEMPLATE = OUT = os.path.join(BASE, 'index.html'); LIVE_JSON = os.path.join(BASE, 'live.json')
PORT, REFRESH, NPICK = 8765, 60, 20

def ensure(pkg, mod=None):
    try: __import__(mod or pkg)
    except ImportError:
        print(f'[설치] {pkg} 설치 중...'); subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', '--upgrade', pkg])
ensure('pandas'); ensure('yfinance')
import pandas as pd, yfinance as yf, urllib.request
from yfinance import EquityQuery as Q

MKTS = {'NASDAQ': {'ex': ['NMS', 'NGM', 'NCM'], 'kr': False, 'minCap': 3e8, 'minVal': 3e6, 'penny': (0.3, 5.0), 'pennyCap': 3e7, 'pennyVal': 1e6},
        'NYSE':   {'ex': ['NYQ'], 'kr': False, 'minCap': 3e8, 'minVal': 3e6, 'penny': (0.3, 5.0), 'pennyCap': 3e7, 'pennyVal': 1e6},
        'KOSPI':  {'ex': ['KSC'], 'kr': True, 'minCap': 5e10, 'minVal': 5e8, 'penny': (300, 2000), 'pennyCap': 1.5e10, 'pennyVal': 3e8},
        'KOSDAQ': {'ex': ['KOE'], 'kr': True, 'minCap': 5e10, 'minVal': 5e8, 'penny': (300, 2000), 'pennyCap': 1.5e10, 'pennyVal': 3e8}}
CATS = [('top', '오늘의 TOP20'), ('value', '저PER·저PBR 가치주'), ('drop', '고점대비 급락 반등'), ('low', '52주 저점권'), ('leader', '주도주 눌림목'), ('penny', '동전주 10배 후보')]
def num(v):
    try:
        v = float(v); return None if math.isnan(v) or math.isinf(v) else v
    except Exception: return None

# ---------- 1) 전종목 시세 ----------
def scan_market(name, M):
    q = Q('and', [Q('is-in', ['exchange'] + M['ex']), Q('gt', ['intradaymarketcap', M['pennyCap']])])
    out, off = [], 0
    while True:
        for t in range(3):
            try: r = yf.screen(q, size=250, offset=off, sortField='intradaymarketcap', sortAsc=False); break
            except Exception as e:
                if t == 2: print('   ! 스크리너 실패', e); r = {'quotes': []}
                time.sleep(2)
        qs = r.get('quotes', []) or []; out += qs; off += 250
        if len(qs) < 250 or off >= 6000: break
    rows = []
    for x in out:
        sym = x.get('symbol', ''); p = num(x.get('regularMarketPrice'))
        if not sym or not p or x.get('quoteType') != 'EQUITY': continue
        nm = x.get('longName') or x.get('shortName') or sym
        if M['kr']:
            code = sym.split('.')[0]
            if len(code) != 6 or not code.isdigit() or code[-1] != '0': continue   # 우선주 제외
            if 'SPAC' in nm.upper() or '스팩' in nm: continue
        else:
            code = sym
            if any(c in sym for c in '-.^') or (len(sym) == 5 and sym[-1] in 'WURQ'): continue
            if any(k in nm for k in ['Acquisition', ' ETF', 'Trust', 'Fund', 'Notes', 'Warrant']): continue
        hi, lo = num(x.get('fiftyTwoWeekHigh')), num(x.get('fiftyTwoWeekLow'))
        fxbad = bool(x.get('financialCurrency')) and x.get('financialCurrency') != x.get('currency')   # ADR 등 통화 불일치 → PER/PBR 신뢰 불가
        av = num(x.get('averageDailyVolume3Month')) or 0; av10 = num(x.get('averageDailyVolume10Day')) or av
        rows.append({'sym': sym, 'code': code, 'name': nm, 'mkt': name, 'p': p, 'hi': hi, 'lo': lo, 'cap': num(x.get('marketCap')) or 0,
                     'pe': None if fxbad else num(x.get('trailingPE')), 'pb': None if fxbad else num(x.get('priceToBook')), 'div': num(x.get('dividendYield')), 'fpe': num(x.get('priceEpsCurrentYear')),
                     'ch52': num(x.get('fiftyTwoWeekChangePercent')), 'ma50': num(x.get('fiftyDayAverage')), 'ma200': num(x.get('twoHundredDayAverage')),
                     'val': av * p, 'volx': (av10 / av) if av else 1, 'chg': num(x.get('regularMarketChangePercent')) or 0})
    for r in rows:
        if r['pb'] is not None and r['pb'] < 0.1: r['pb'] = None
        if r['pe'] is not None and 0 < r['pe'] < 1: r['pe'] = None
        r['dd'] = (r['p'] / r['hi'] - 1) * 100 if r['hi'] else None
        r['fl'] = (r['p'] / r['lo'] - 1) * 100 if r['lo'] else None
    return rows

# ---------- 2) 한국 재무(PER·PBR) ----------
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36', 'Referer': 'https://m.stock.naver.com/'}
def naver_fund(code):
    u = f'https://m.stock.naver.com/api/stock/{code}/integration'
    j = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=6).read().decode('utf-8'))
    d = {}
    for it in j.get('totalInfos', []) or []:
        v = str(it.get('value', '')).replace(',', '').replace('배', '').replace('%', '').replace('원', '').strip()
        d[it.get('code')] = num(v) if v not in ('', '-', 'N/A') else None
    return {'name': j.get('stockName'), 'pe': d.get('per'), 'pb': d.get('pbr'), 'div': d.get('dividendYieldRatio')}
def yahoo_fund(r):
    try:
        t = yf.Ticker(r['sym']); i = t.info or {}; mc = num(i.get('marketCap')) or r['cap']; ni = num(i.get('netIncomeToCommon'))
        eq = None; bs = t.balance_sheet
        for k in ('Stockholders Equity', 'Common Stock Equity'):
            if k in bs.index:
                v = bs.loc[k].dropna()
                if len(v): eq = num(v.iloc[0]); break
        return {'pe': (mc / ni) if mc and ni and ni > 0 else (-1 if ni and ni < 0 else None), 'pb': (mc / eq) if mc and eq and eq > 0 else None, 'roe': (ni / eq * 100) if ni and eq and eq > 0 else None}
    except Exception: return {}
def kr_fundamentals(rows, n=260):
    pool = sorted([r for r in rows if r['cap'] >= 3e10], key=lambda r: -r['val'])[:n]
    ok = 0; naver = True
    try: naver_fund(pool[0]['code'])
    except Exception: naver = False
    print(f'   재무 소스: {"네이버 금융" if naver else "Yahoo 재무제표(네이버 접속 불가 → 상위 80종목만)"}')
    if not naver: pool = pool[:80]
    for k, r in enumerate(pool):
        try:
            f = naver_fund(r['code']) if naver else yahoo_fund(r)
            if naver and f.get('name'): r['kname'] = f['name']
            for key in ('pe', 'pb', 'div', 'roe'):
                if f.get(key) is not None: r[key] = f[key]
            ok += 1
        except Exception: pass
        if not naver and k % 10 == 9: print(f'     {k+1}/{len(pool)}')
    return ok

# ---------- 3) 1차 후보 (시세 기반) ----------
def liquid(r, M): return r['cap'] >= M['minCap'] and r['val'] >= M['minVal']
def prelim(rows, M):
    L = [r for r in rows if liquid(r, M) and r['dd'] is not None]
    caps = sorted([r['cap'] for r in L], reverse=True); topcap = caps[max(0, len(caps) // 10 - 1)] if caps else 0
    C = {}
    C['value'] = sorted([r for r in L if r['pe'] and 0 < r['pe'] <= 15 and r['pb'] and 0 < r['pb'] <= 1.5], key=lambda r: r['pe'] / 15 + r['pb'] / 1.5)[:45]
    C['drop'] = sorted([r for r in L if r['dd'] <= -40 and (r['fl'] or 0) >= 5], key=lambda r: r['dd'])[:45]
    C['low'] = sorted([r for r in L if r['fl'] is not None and r['fl'] <= 12 and r['dd'] <= -20], key=lambda r: r['fl'])[:45]
    C['leader'] = sorted([r for r in L if (r['cap'] >= topcap or (r['ch52'] or 0) >= 40) and -35 <= r['dd'] <= -8 and (r['ma200'] is None or r['p'] >= r['ma200'] * 0.97)], key=lambda r: -((r['ch52'] or 0) * 0.5 + (r['cap'] >= topcap) * 40))[:45]
    lo, hi = M['penny']
    C['penny'] = sorted([r for r in rows if lo <= r['p'] <= hi and r['cap'] >= M['pennyCap'] and r['val'] >= M['pennyVal'] and (r['dd'] or 0) <= -35 and (r['fl'] or 0) >= 8], key=lambda r: -r['volx'])[:45]
    return C, topcap

# ---------- 4) 히스토리 지표 ----------
def rsi(c, n=14):
    if len(c) < n + 1: return None
    g = l = 0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]; g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]; g = (g * (n - 1) + max(d, 0)) / n; l = (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)
def history(syms, kr):
    out = {}
    for k in range(0, len(syms), 120):
        chunk = syms[k:k + 120]
        try: df = yf.download(chunk, period='1y', interval='1d', group_by='ticker', auto_adjust=False, threads=True, progress=False)
        except Exception as e: print('   ! 히스토리 실패', e); continue
        for s in chunk:
            try:
                d = (df[s] if isinstance(df.columns, pd.MultiIndex) else df).dropna(subset=['Close'])
                bars = []
                for ts, r in d.iterrows():
                    c = num(r['Close'])
                    if c is None: continue
                    o = num(r['Open']) or c; h = max(num(r['High']) or c, o, c); l = min(num(r['Low']) or c, o, c)
                    rd = (lambda x: round(x)) if kr else (lambda x: round(x, 4 if x < 1 else 2))
                    t = int(datetime.datetime(ts.year, ts.month, ts.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
                    if bars and bars[-1][0] == t: bars[-1] = [t, rd(o), rd(h), rd(l), rd(c), int(num(r['Volume']) or 0)]
                    else: bars.append([t, rd(o), rd(h), rd(l), rd(c), int(num(r['Volume']) or 0)])
                if len(bars) >= 60: out[s] = bars
            except Exception: pass
    return out
def enrich(r, bars):
    c = [b[4] for b in bars]; v = [b[5] for b in bars]; p = c[-1]
    ma = lambda n: sum(c[-n:]) / n if len(c) >= n else None
    r['rsi'] = rsi(c); r['ma20'] = ma(20); r['ma60'] = ma(60); r['ma120'] = ma(120)
    lo60 = min(b[3] for b in bars[-60:]); r['bounce'] = (p / lo60 - 1) * 100 if lo60 else 0
    r['ret20'] = (p / c[-21] - 1) * 100 if len(c) > 21 else 0
    a5 = sum(v[-5:]) / 5; a60 = sum(v[-60:]) / 60; r['volx5'] = a5 / a60 if a60 else 1
    hi = max(b[2] for b in bars); lo = min(b[3] for b in bars); r['hi'] = hi; r['lo'] = lo; r['p'] = p
    r['dd'] = (p / hi - 1) * 100; r['fl'] = (p / lo - 1) * 100
    r['ma60gap'] = (p / r['ma60'] - 1) * 100 if r['ma60'] else None

# ---------- 5) 최종 점수·사유 ----------
def clamp(v, a, b): return max(a, min(b, v))
def score(cat, r, topcap):
    rs = r.get('rsi') or 50; pe, pb = r.get('pe'), r.get('pb')
    if cat == 'value':
        if not (pe and pe > 0 and pb and pb > 0): return None
        return 45 * (1 - clamp(pe, 0, 20) / 20) + 35 * (1 - clamp(pb, 0, 2) / 2) + 10 * clamp(r.get('div') or 0, 0, 6) / 6 + (10 if r['p'] >= (r.get('ma20') or 0) else 0)
    if cat == 'drop':
        if r['dd'] > -40 or r['bounce'] < 3: return None
        return clamp(-r['dd'], 40, 85) * 0.6 + clamp(r['bounce'], 0, 35) + (12 if 35 <= rs <= 60 else 0) + (8 if r['p'] >= (r.get('ma20') or 1e18) else 0) + (8 if pe and pe > 0 else 0)
    if cat == 'low':
        if r['fl'] > 12: return None
        return (12 - r['fl']) * 3 + clamp(-r['dd'], 0, 70) * 0.35 + (12 if rs < 35 else 0) + (10 if pe and pe > 0 else 0) + (6 if pb and pb < 1 else 0)
    if cat == 'leader':
        lead = (r['cap'] >= topcap) or (r.get('ch52') or 0) >= 40
        if not lead or not (-35 <= r['dd'] <= -8): return None
        if r.get('ma120') and r['p'] < r['ma120'] * 0.95: return None
        return clamp(r.get('ch52') or 0, 0, 200) * 0.2 + (25 if r['cap'] >= topcap else 0) + (20 - abs(r['dd'] + 18)) + (12 if 30 <= rs <= 50 else 0) + (8 if r.get('ma60gap') is not None and abs(r['ma60gap']) <= 6 else 0)
    if cat == 'penny':
        if r['bounce'] < 10: return None
        return clamp(r.get('volx5') or 1, 0, 5) * 12 + clamp(-r['dd'], 35, 95) * 0.35 + clamp(r['bounce'], 0, 80) * 0.4 + (10 if r['p'] >= (r.get('ma20') or 1e18) else 0) + (6 if pe and pe > 0 else 0)
def reasons(cat, r, kr):
    f = lambda v, d=1: '-' if v is None else f'{v:.{d}f}'
    R = []
    if r.get('pe') and r['pe'] > 0: R.append(f"PER {f(r['pe'])}")
    if r.get('pb') and r['pb'] > 0: R.append(f"PBR {f(r['pb'],2)}")
    R.append(f"고점대비 {f(r['dd'],0)}%"); R.append(f"저점대비 +{f(r['fl'],0)}%")
    if r.get('rsi') is not None: R.append(f"RSI {f(r['rsi'],0)}")
    if cat == 'leader' and r.get('ch52') is not None: R.append(f"52주 {r['ch52']:+.0f}%")
    if cat in ('penny', 'drop') and r.get('volx5'): R.append(f"거래량 {r['volx5']:.1f}배")
    risk = []
    if r.get('pe') is not None and r['pe'] < 0: risk.append('적자')
    if cat == 'penny': risk.append('동전주·고변동성')
    if r['dd'] <= -70: risk.append('고점대비 -70% 이하')
    if (r.get('rsi') or 50) < 25: risk.append('과매도 지속')
    if (r.get('rsi') or 50) > 70: risk.append('단기 과열(RSI 70↑)')
    return R, risk
CATW = {'value': 5, 'leader': 5, 'drop': 4, 'low': 3, 'penny': 3}

def build_market(name, M):
    print(f'\n[{name}] 전종목 스캔...'); rows = scan_market(name, M); print(f'   {len(rows)}종목 수집'); ROWS[name] = rows
    if M['kr']: print(f'   재무 {kr_fundamentals(rows)}종목 반영')
    C, topcap = prelim(rows, M)
    syms = list(dict.fromkeys(r['sym'] for L in C.values() for r in L)); print(f'   1차 후보 {len(syms)}종목 → 1년 일봉 수신')
    H = history(syms, M['kr']); bym = {r['sym']: r for r in rows}
    final = {}; info = {}
    for cat, L in C.items():
        sc = []
        for r in L:
            if r['sym'] not in H: continue
            enrich(r, H[r['sym']]); s = score(cat, r, topcap)
            if s is not None: sc.append((s, r))
        sc.sort(key=lambda x: -x[0]); final[cat] = [(round(s, 1), r) for s, r in sc[:NPICK]]
    top, seen = [], set()
    for rnd in range(max(CATW.values())):   # 카테고리 순환 배치 (가치→눌림목→급락→저점→동전주)
        for cat, n in CATW.items():
            L = final.get(cat, [])
            if rnd < n and rnd < len(L):
                s, r = L[rnd]
                if r['sym'] not in seen: seen.add(r['sym']); top.append((s, r, cat))
    rest = sorted([(s, r, c) for c, L in final.items() for s, r in L if r['sym'] not in seen], key=lambda x: -x[0])
    for s, r, c in rest:
        if len(top) >= NPICK: break
        if r['sym'] not in seen: seen.add(r['sym']); top.append((s, r, c))
    picks = {'top': [r['code'] for s, r, c in top]}
    for cat, L in final.items(): picks[cat] = [r['code'] for s, r in L]
    used = set(c for L in picks.values() for c in L)
    for cat, L in final.items():
        for s, r in L:
            R, risk = reasons(cat, r, M['kr']); e = info.setdefault(r['code'], {'cats': [], 'score': {}, 'reason': R, 'risk': risk})
            e['cats'].append(cat); e['score'][cat] = s
    for s, r, c in top: info[r['code']]['topCat'] = c
    bars, fund, watch = {}, {}, []
    for code in used:
        r = next(x for x in rows if x['code'] == code)
        bars[code] = H[r['sym']]
        fund[code] = {'per': r.get('pe'), 'pbr': r.get('pb'), 'roe': r.get('roe') if r.get('roe') is not None else ((r['pb'] / r['pe'] * 100) if r.get('pe') and r['pe'] > 0 and r.get('pb') else None), 'div': r.get('div'), 'debt': None, 'opm': None, 'mcap': r['cap'], 'src': 'Yahoo/네이버'}
        m = info[code]; m['m'] = {k: (round(r[k], 2) if isinstance(r.get(k), float) else r.get(k)) for k in ('dd', 'fl', 'rsi', 'ch52', 'volx5', 'bounce', 'ret20', 'ma60gap', 'hi', 'lo', 'val')}
        watch.append({'code': code, 'name': r.get('kname') or r['name'], 'mkt': name, 'api': r['sym'], 'ysym': r['sym']})
    if M['kr']:
        need = [w for w in watch if not any(r.get('kname') for r in rows if r['code'] == w['code'])]
        try:
            naver_fund(need[0]['code']) if need else None
            for w in need:
                try:
                    f = naver_fund(w['code']); w['name'] = f.get('name') or w['name']
                except Exception: pass
        except Exception: pass
    for cat, lab in CATS: print(f'   {lab}: {len(picks.get(cat, []))}종목')
    return picks, info, bars, fund, watch

def build(all_):
    if not os.path.exists(TEMPLATE): sys.exit(f'[오류] 템플릿 파일이 없습니다: hts_template.html (또는 index.html) 을 저장소 최상위에 올려주세요')
    t = open(TEMPLATE, encoding='utf-8').read()
    if '/*@BAKED*/' not in t: sys.exit(f'[오류] {os.path.basename(TEMPLATE)} 에 /*@BAKED*/ 표시가 없습니다. 원본 hts_template.html 을 다시 올려주세요')
    baked = {'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'cats': CATS, 'picks': {}, 'info': {}, 'bars': {}, 'fund': {}, 'watch': [], 'src': 'Yahoo Finance 스크리너(전종목) · 한국 재무 네이버 금융'}
    for name, (picks, info, bars, fund, watch) in all_.items():
        baked['picks'][name] = picks; baked['info'].update(info); baked['bars'].update(bars); baked['fund'].update(fund); baked['watch'] += watch
    js = json.dumps(baked, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    a, b = '/*@BAKED*/', '/*@/BAKED*/'; i, j = t.index(a) + len(a), t.index(b)
    open(OUT, 'w', encoding='utf-8').write(t[:i] + js + t[j:]); print(f'\n생성 완료: {OUT} ({os.path.getsize(OUT)/1e6:.1f} MB)')
    return baked

def live_loop(watch):
    syms = [w['ysym'] for w in watch]
    while True:
        try:
            df = yf.download(syms, period='5d', interval='1d', group_by='ticker', auto_adjust=False, threads=True, progress=False); q = {}
            for w in watch:
                try:
                    d = df[w['ysym']].dropna(subset=['Close']); ts = d.index[-1]; r = d.iloc[-1]
                    t = int(datetime.datetime(ts.year, ts.month, ts.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
                    q[w['code']] = {'t': t, 'o': num(r['Open']), 'h': num(r['High']), 'l': num(r['Low']), 'p': num(r['Close']), 'v': int(num(r['Volume']) or 0), 'src': 'Yahoo'}
                except Exception: pass
            json.dump({'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'q': q}, open(LIVE_JSON, 'w', encoding='utf-8'), ensure_ascii=False)
            print(f'   [LIVE] {datetime.datetime.now():%H:%M:%S} {len(q)}종목 갱신')
        except Exception as e: print('   [LIVE] 오류', e)
        time.sleep(REFRESH)

def read_baked():
    t = open(OUT, encoding='utf-8').read(); a, b = '/*@BAKED*/', '/*@/BAKED*/'
    raw = t[t.index(a) + len(a):t.index(b)].strip()
    return None if raw in ('', 'null') else json.loads(raw)
def quotes():
    """추천 종목 현재가만 빠르게 갱신 → live.json (페이지가 20초마다 읽어감)"""
    B = read_baked()
    if not B: print('index.html 에 추천 데이터가 없어 전체 스캔부터 실행합니다'); return False
    W = B['watch']; syms = [w['ysym'] for w in W]; q = {}
    df = yf.download(syms, period='5d', interval='1d', group_by='ticker', auto_adjust=False, threads=True, progress=False)
    for w in W:
        try:
            d = (df[w['ysym']] if isinstance(df.columns, pd.MultiIndex) else df).dropna(subset=['Close']); ts = d.index[-1]; r = d.iloc[-1]
            t = int(datetime.datetime(ts.year, ts.month, ts.day, tzinfo=datetime.timezone.utc).timestamp() * 1000)
            q[w['code']] = {'t': t, 'o': num(r['Open']), 'h': num(r['High']), 'l': num(r['Low']), 'p': num(r['Close']), 'v': int(num(r['Volume']) or 0), 'src': 'Yahoo'}
        except Exception: pass
    json.dump({'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'scanAt': B['at'], 'q': q}, open(LIVE_JSON, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'현재가 {len(q)}/{len(W)}종목 갱신 → live.json'); return True

def main():
    if '--quotes' in sys.argv and quotes(): return
    if '--pred' in sys.argv:   # 선행 예측만 다시 계산 (index.html 은 그대로)
        B = read_baked() if os.path.exists(OUT) else None
        build_pred(B['at'] if B else None); return
    only = [a.upper() for a in sys.argv[1:] if a.upper() in MKTS]
    t0 = time.time(); res = {}
    for name, M in MKTS.items():
        if only and name not in only: continue
        try: res[name] = build_market(name, M)
        except Exception as e: print(f'   ! {name} 실패: {e}')
    if not res: sys.exit('[오류] 4개 시장 모두 수집 실패 (Yahoo 일시 차단 가능) → 잠시 후 다시 실행')
    baked = build(res); print(f'총 소요 {time.time()-t0:.0f}초')
    try: build_pred(baked['at'])   # 선행 예측 (실패해도 추천 스캔 결과는 그대로)
    except Exception as e: print('   ! 선행 예측 생략:', e)
    json.dump({'at': '', 'scanAt': baked['at'], 'q': {}}, open(LIVE_JSON, 'w', encoding='utf-8'))
    if '--live' in sys.argv:
        import http.server, socketserver
        os.chdir(BASE); threading.Thread(target=live_loop, args=(baked['watch'],), daemon=True).start()
        class H(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a): pass
            def end_headers(self): self.send_header('Cache-Control', 'no-store'); super().end_headers()
        url = f'http://127.0.0.1:{PORT}/index.html'; print(f'[LIVE] {url} · 창을 닫으면 중지'); webbrowser.open(url)
        with socketserver.TCPServer(('127.0.0.1', PORT), H) as s: s.serve_forever()
    elif '--nobrowser' not in sys.argv:
        webbrowser.open('file:///' + OUT.replace('\\', '/'))

# =====================================================================
# ---------- 6) 선행 예측 (전종목) → pred.json ----------
#   스캔 때 함께 실행. 전종목(유동성 필터) 2년 일봉으로 RSI·스토캐스틱·MACD·볼린저·MFI·OBV·
#   매수압력·다이버전스·이격도 신호를 만들고, 종목별 과거 적중도로 가중치를 조절한 뒤
#   '과거 비슷한 신호가 나온 날의 5일·20일 뒤 수익률'로 상승 확률과 예상 범위를 계산한다.
#   ※ 확률적 추정이며 투자 권유가 아님. 실패해도 기존 추천 스캔에는 영향 없음.
# =====================================================================
PRED_JSON = os.path.join(BASE, 'pred.json')
ROWS = {}   # build_market 이 시장별 전종목 시세를 여기에 남김
MKCODE = {'KOSPI': 'K', 'KOSDAQ': 'Q', 'NASDAQ': 'N', 'NYSE': 'Y'}
RULES = [('rsiRev', 'RSI 과매수·과매도', 1.0), ('rsiMom', 'RSI 방향', 0.8), ('stoch', '스토캐스틱', 0.8), ('macd', 'MACD', 1.0),
         ('trend', '이동평균 정배열', 1.1), ('bb', '볼린저밴드 위치', 0.7), ('flow', '거래량 매수세', 1.0), ('mfi', 'MFI(자금흐름)', 0.8),
         ('div', 'RSI 다이버전스', 0.7), ('gap', '20일선 이격(되돌림)', 0.8)]
LVN = ['20일 저점', '20일 고점', '60일 저점', '60일 고점', '20일선', '60일선', '120일선', '볼린저 상단', '볼린저 하단', '52주 고점', '52주 저점']

def _rd(x, kr):
    if x is None or not math.isfinite(x): return None
    return round(x) if kr and abs(x) >= 100 else (round(x, 2) if abs(x) >= 1 else round(x, 4))

def pred_engine(df, kr):
    """df: Open/High/Low/Close/Volume 일봉. 결과 dict 또는 None"""
    import numpy as np
    d = df.dropna(subset=['Close'])
    d = d[d['Close'] > 0]
    if len(d) < 130: return None
    c = d['Close'].astype(float); o = d['Open'].fillna(c).astype(float)
    h = pd.concat([d['High'].fillna(c), o, c], axis=1).max(axis=1); l = pd.concat([d['Low'].fillna(c), o, c], axis=1).min(axis=1)
    v = d['Volume'].fillna(0).astype(float)
    n = len(c); C = c.values
    dl = c.diff()
    ag = dl.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); al = (-dl.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    R = 100 - 100 / (1 + ag / al.replace(0, np.nan)); R = R.fillna(100)
    hh, ll = h.rolling(14).max(), l.rolling(14).min()
    K = (100 * (c - ll) / (hh - ll).replace(0, np.nan)).fillna(50).rolling(3).mean(); D = K.rolling(3).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean(); hist = macd - macd.ewm(span=9, adjust=False).mean()
    m20, m60, m120 = c.rolling(20).mean(), c.rolling(60).mean(), c.rolling(120).mean(); sd = c.rolling(20).std(ddof=0)
    pb = ((c - (m20 - 2 * sd)) / (4 * sd).replace(0, np.nan)).fillna(0.5)
    obv = (np.sign(dl.fillna(0)) * v).cumsum(); ob = ((obv - obv.shift(20)) / v.rolling(20).sum().replace(0, np.nan)).fillna(0)
    tp = (h + l + c) / 3; mf = tp * v; pos = mf.where(tp > tp.shift(), 0).rolling(14).sum(); neg = mf.where(tp < tp.shift(), 0).rolling(14).sum()
    mfi = (100 - 100 / (1 + pos / neg.replace(0, np.nan))).fillna(100).where(pos + neg > 0, 50)
    clv = (((c - l) - (h - c)) / (h - l).replace(0, np.nan)).fillna(0); clv10 = clv.rolling(10).mean()
    upw = v.where(dl > 0, 0) + 0.5 * v.where(dl == 0, 0); upv = (upw.rolling(10).sum() / v.rolling(10).sum().replace(0, np.nan)).fillna(0.5)
    ret = np.log(c / c.shift()); sd20 = (ret.rolling(20).std() * 100).fillna(2).clip(lower=0.3)
    cl = lambda x, a=-1, b=1: x.clip(a, b)
    S = {}
    S['rsiRev'] = cl((32 - R) / 14, 0, 1).where(R < 30, 0) - cl((R - 68) / 14, 0, 1).where(R > 70, 0)
    S['rsiMom'] = cl((R - R.shift(5)) / 15) * np.where((R > 75) | (R < 25), 0.5, 1)
    s = cl((K - D) / 8); s = s.where(~((K > 85) & (s > 0)), s * 0.4); s = s.where(~((K < 15) & (s < 0)), s * 0.4)
    s = s.where(~((K < 20) & (K > D)), np.maximum(s, 0.5)); s = s.where(~((K > 80) & (K < D)), np.minimum(s, -0.5)); S['stoch'] = s
    s = 0.5 * cl(hist / c * 100 / 0.6) + 0.5 * cl((hist - hist.shift(3)) / c * 100 / 0.25)
    s = s.where(~((hist > 0) & (hist.shift() <= 0)), np.maximum(s, 0.7)); s = s.where(~((hist < 0) & (hist.shift() >= 0)), np.minimum(s, -0.7)); S['macd'] = s
    S['trend'] = pd.Series(np.where(c > m20, .35, -.35) + np.where(m20 > m60, .3, -.3), index=c.index) + .35 * cl((m20 / m20.shift(5) - 1) * 100 / 1.5)
    S['bb'] = cl((0.1 - pb) * 5 + 0.3).where(pb < 0.1, 0) - cl((pb - 0.9) * 5 + 0.3).where(pb > 0.9, 0)
    S['flow'] = cl(0.8 * ob + 0.75 * clv10 + 1.2 * (upv - 0.5))
    S['mfi'] = pd.Series(np.where(mfi < 20, cl((25 - mfi) / 15 + 0.2), np.where(mfi > 80, -cl((mfi - 75) / 15 + 0.2), 0.5 * cl((mfi - mfi.shift(5)) / 25))), index=c.index)
    lowR, lowP = l.rolling(10).min(), l.shift(15).rolling(25).min(); hiR, hiP = h.rolling(10).max(), h.shift(15).rolling(25).max()
    rR, rP, xR, xP = R.rolling(10).min(), R.shift(15).rolling(25).min(), R.rolling(10).max(), R.shift(15).rolling(25).max()
    S['div'] = pd.Series(np.where((lowR < lowP) & (rR > rP + 3), 0.8, 0) + np.where((hiR > hiP) & (xR < xP - 3), -0.8, 0), index=c.index)
    S['gap'] = -cl((c / m20 - 1) * 100 / (sd20 * 4))
    keys = [k for k, _, _ in RULES]; base = np.array([w for _, _, w in RULES])
    M = np.nan_to_num(np.vstack([S[k].values.astype(float) for k in keys]), nan=0.0)   # (규칙, 날짜)
    st = 70
    f5 = np.log(c.shift(-5) / c).values; f20 = np.log(c.shift(-20) / c).values
    def ics(a, b):
        out = []
        for k in range(len(keys)):
            x, y = M[k, a:b], f5[a:b]; ok = np.isfinite(y)
            x, y = x[ok], y[ok]
            out.append(0.0 if len(x) < 30 or x.std() < 1e-9 or y.std() < 1e-12 else float(np.corrcoef(x, y)[0, 1]))
        return np.nan_to_num(np.array(out))
    end5 = n - 5
    ic = ics(st, end5); W = base * np.clip(1 + ic * 6, 0.3, 2.2)
    comp = (W[:, None] * M).sum(0) / W.sum()
    # 걸어가며 검증(앞 절반으로 가중치 → 뒤 절반 방향 적중률)
    oos = [None, 0]
    mid = (st + end5) // 2
    if end5 - st >= 120:
        Wf = base * np.clip(1 + ics(st, mid) * 6, 0.3, 2.2); cf = (Wf[:, None] * M).sum(0) / Wf.sum()
        x, y = cf[mid:end5], f5[mid:end5]; ok = np.isfinite(y) & (np.abs(x) > 0.1)
        if ok.sum() >= 15: oos = [round(float((np.sign(x[ok]) == np.sign(y[ok])).mean()) * 100), int(ok.sum())]
    sdd = float(np.nanstd(ret.values[-60:]))   # 일간 로그수익률 표준편차
    now = float(comp[-1]); res = {}
    for hz, F in ((5, f5), (20, f20)):
        X, Y = comp[st:n - hz], F[st:n - hz]; ok = np.isfinite(Y); X, Y = X[ok], Y[ok]
        if len(Y) < 30: return None
        bm, bu = float(Y.mean()), float((Y > 0).mean())
        wdt = 0.10
        while True:
            sel = np.abs(X - now) <= wdt
            if sel.sum() >= 25 or wdt >= 0.35: break
            wdt += 0.05
        Ys = Y[sel]; na = int(len(Ys)); am = float(Ys.mean()) if na else bm; au = float((Ys > 0).mean()) if na else bu
        kk = 12; sh = sdd * math.sqrt(hz)
        pu = (na * au + kk * bu) / (na + kk); mu = (na * am + kk * now * 0.25 * sh) / (na + kk)
        res[hz] = {'pu': min(0.8, max(0.2, pu)), 'mu': mu, 'n': na, 'au': au, 'bu': bu}
    sent = pd.concat([R, K, mfi, 50 + 50 * clv10, 100 * upv, 50 + 50 * cl(ob * 2)], axis=1).mean(axis=1).clip(0, 100)
    i = n - 1; p = float(C[i])
    lv = []
    def add(x, k):
        if x is not None and math.isfinite(x) and x > 0: lv.append((float(x), k))
    add(l.iloc[-20:].min(), 0); add(h.iloc[-20:].max(), 1); add(l.iloc[-60:].min(), 2); add(h.iloc[-60:].max(), 3)
    add(m20.iloc[i], 4); add(m60.iloc[i], 5); add(m120.iloc[i], 6); add((m20 + 2 * sd).iloc[i], 7); add((m20 - 2 * sd).iloc[i], 8)
    add(h.iloc[-250:].max(), 9); add(l.iloc[-250:].min(), 10)
    sup = sorted([x for x in lv if x[0] < p * 0.998], key=lambda x: -x[0])[:3]; rs = sorted([x for x in lv if x[0] > p * 1.002], key=lambda x: x[0])[:3]
    f = lambda x, dd=1: None if x is None or not math.isfinite(float(x)) else round(float(x), dd)
    ma = (1 if p > m20.iloc[i] else 0) + (1 if m20.iloc[i] > m60.iloc[i] else 0) + (1 if m60.iloc[i] > (m120.iloc[i] if math.isfinite(m120.iloc[i]) else -1) else 0)
    va = v.iloc[-60:].mean()
    return {
        't': c.index[-1].strftime('%Y-%m-%d'), 'p': _rd(p, kr), 'ch': f((C[i] / C[i - 1] - 1) * 100, 2),
        's': round(now * 100), 'se': round(float(sent.iloc[i])), 'sh': [round(float(x)) for x in sent.iloc[-40:].fillna(50)],
        'pu': [round(res[5]['pu'] * 100), round(res[20]['pu'] * 100)], 'mu': [f(res[5]['mu'] * 100, 2), f(res[20]['mu'] * 100, 2)],
        'sd': f(sdd * 100, 2), 'an': [res[5]['n'], res[20]['n']], 'au': [round(res[5]['au'] * 100), round(res[20]['au'] * 100)],
        'bu': [round(res[5]['bu'] * 100), round(res[20]['bu'] * 100)], 'oos': oos,
        'sg': [round(float(M[k, i]) * 100) for k in range(len(keys))], 'ic': [round(float(x) * 100) for x in ic], 'w': [round(float(x) * 10) for x in W],
        'iv': {'rsi': f(R.iloc[i]), 'rsi5': f(R.iloc[i - 5]), 'k': f(K.iloc[i]), 'd': f(D.iloc[i]), 'mfi': f(mfi.iloc[i]), 'hn': f(hist.iloc[i] / p * 100, 2),
               'pb': f(pb.iloc[i], 2), 'g20': f((p / m20.iloc[i] - 1) * 100), 'g60': f((p / m60.iloc[i] - 1) * 100), 'ob': f(ob.iloc[i], 2),
               'clv': f(clv10.iloc[i], 2), 'upv': f(upv.iloc[i] * 100, 0), 'vr': f(v.iloc[-5:].mean() / va if va else 1, 2), 'ma': ma,
               'r20': f((C[i] / C[i - 20] - 1) * 100), 'r5': f((C[i] / C[i - 5] - 1) * 100)},
        'lv': {'s': [[_rd(x, kr), k] for x, k in sup], 'r': [[_rd(x, kr), k] for x, k in rs]},
        'cl': [_rd(float(x), kr) for x in C[-60:]], 'av': round(float(v.iloc[-20:].mean())),
    }

def naver_names():
    """한국 전종목 한글 이름 (네이버 시가총액 목록). 실패하면 빈 dict"""
    out = {}
    for mk in ('KOSPI', 'KOSDAQ'):
        for pg in range(1, 40):
            try:
                j = json.loads(urllib.request.urlopen(urllib.request.Request(f'https://m.stock.naver.com/api/stocks/marketValue/{mk}?page={pg}&pageSize=100', headers=UA), timeout=8).read().decode('utf-8'))
            except Exception:
                return out
            st = j.get('stocks') or []
            for s_ in st: out[s_.get('itemCode')] = s_.get('stockName')
            if len(st) < 100: break
    return out

def naver_flow(code):
    j = json.loads(urllib.request.urlopen(urllib.request.Request(f'https://m.stock.naver.com/api/stock/{code}/trend?pageSize=20', headers=UA), timeout=6).read().decode('utf-8'))
    g = lambda x: num(str(x or '0').replace(',', '').replace('+', '').replace('%', '')) or 0
    L = [(g(x.get('foreignerPureBuyQuant')), g(x.get('organPureBuyQuant')), g(x.get('individualPureBuyQuant'))) for x in (j or [])]
    if not L: return None
    sm = lambda a, b: [round(sum(x[k] for x in L[a:b])) for k in range(3)]
    return {'d5': sm(0, 5), 'd20': sm(0, 20), 'fr': num(str((j or [{}])[0].get('foreignerHoldRatio', '')).replace('%', '')),
            'days': [[x.get('bizdate'), g(x.get('foreignerPureBuyQuant')), g(x.get('organPureBuyQuant'))] for x in j[:10]]}

def build_pred(scan_at=None):
    t0 = time.time()
    if not ROWS:
        for name, M in MKTS.items():
            try: ROWS[name] = scan_market(name, M); print(f'   [예측] {name} {len(ROWS[name])}종목 시세')
            except Exception as e: print(f'   [예측] {name} 시세 실패 {e}')
    uni = []
    for name, rows in ROWS.items():
        M = MKTS[name]
        if M['kr']: L = [r for r in rows if r['cap'] >= 3e10 and r['val'] >= 2e8]
        else: L = [r for r in rows if r['cap'] >= 3e8 and r['val'] >= 2e6]
        uni += L; print(f'   [예측] {name} 대상 {len(L)}종목')
    kn = naver_names(); print(f'   [예측] 한글 종목명 {len(kn)}개')
    out = {}; done = 0
    for kr in (True, False):
        U = [r for r in uni if MKTS[r['mkt']]['kr'] == kr]
        for k in range(0, len(U), 150):
            chunk = U[k:k + 150]; syms = [r['sym'] for r in chunk]
            df = None
            for t in range(3):
                try: df = yf.download(syms, period='2y', interval='1d', group_by='ticker', auto_adjust=False, threads=True, progress=False); break
                except Exception as e: print('   ! 예측 일봉 실패', e); time.sleep(3)
            if df is None: continue
            for r in chunk:
                try:
                    d = df[r['sym']] if isinstance(df.columns, pd.MultiIndex) else df
                    e = pred_engine(d, kr)
                    if not e: continue
                    e['m'] = MKCODE[r['mkt']]; e['n'] = kn.get(r['code']) or r.get('kname') or r['name']
                    e['cap'] = round(r['cap'] / (1e8 if kr else 1e6)); e['pe'] = _rd(r.get('pe'), False) if r.get('pe') and r['pe'] > 0 else None
                    out[r['code']] = e; done += 1
                except Exception: pass
            print(f'   [예측] {"국내" if kr else "미국"} {min(k + 150, len(U))}/{len(U)} · 누적 {done}종목 ({time.time() - t0:.0f}초)')
    # 국내 외국인·기관 수급 (거래대금 상위 500종목)
    try:
        krc = sorted([r for r in uni if MKTS[r['mkt']]['kr'] and r['code'] in out], key=lambda r: -r['val'])[:500]
        naver_flow(krc[0]['code'])
        from concurrent.futures import ThreadPoolExecutor
        def job(r):
            try: return r['code'], naver_flow(r['code'])
            except Exception: return r['code'], None
        nf = 0
        with ThreadPoolExecutor(8) as ex:
            for code, fl in ex.map(job, krc):
                if fl:
                    e = out[code]; fl['av'] = e['av']; e['fl'] = fl; nf += 1
                    fs = max(-1, min(1, (fl['d5'][0] + fl['d5'][1]) / (e['av'] * 5 or 1) * 8))   # 외국인+기관 5일 순매수 / 5일 거래량
                    sh = (e['sd'] or 2) / 100 * math.sqrt(5)
                    e['pu'][0] = max(20, min(80, round(e['pu'][0] + 4 * fs))); e['pu'][1] = max(20, min(80, round(e['pu'][1] + 3 * fs)))
                    e['mu'][0] = round(e['mu'][0] + fs * 0.1 * sh * 100, 2); e['fs'] = round(fs * 100)
        print(f'   [예측] 수급 {nf}종목 반영')
    except Exception as e: print('   [예측] 수급 생략', e)
    P = {'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'scanAt': scan_at, 'rules': [[k, nm] for k, nm, _ in RULES], 'lvn': LVN, 'n': len(out), 's': out}
    json.dump(P, open(PRED_JSON, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'예측 완료: {len(out)}종목 → pred.json ({os.path.getsize(PRED_JSON) / 1e6:.1f} MB, {time.time() - t0:.0f}초)')

if __name__ == '__main__':
    main()
