import json, numpy as np, pandas as pd
from math import comb
import pathlib; C=str(pathlib.Path(__file__).resolve().parents[3] / "docs/data/charts") + "/"
def load(f):
    d=json.load(open(C+f+".json"))
    w=pd.Series({pd.Timestamp(r[0]):r[4] for r in d["w"]}); dd=pd.Series({pd.Timestamp(r[0]):r[4] for r in d["d"]})
    return pd.concat([w[w.index<dd.index[0]], dd]).sort_index(), dd
for name in ["TWII","SPY","0050_TW"]:
    full, daily = load(name)
    wk = full.resample("W-FRI").last().dropna()
    print(f"\n== {name} {wk.index[0].date()}..{wk.index[-1].date()}  weeks={len(wk)}")
    for lab,n in [("1週",1),("1月",4),("3月",13),("1年",52),("3年",156),("5年",260)]:
        r = wk.pct_change(n).dropna()
        print(f"  {lab}: 上漲比例 {(r>0).mean():.0%}  最差 {r.min():+.0%}  中位 {r.median():+.1%}  n={len(r)}")
    # drawdowns
    dd_ = wk/wk.cummax()-1
    print("  最大回檔", f"{dd_.min():.0%}", dd_.idxmin().date())
    # top drawdown episodes and recovery weeks
    peak=wk.cummax(); eps=[]; i=0; v=wk.values; idx=wk.index
    inn=False
    for t in range(len(v)):
        if v[t] < peak.iloc[t]*0.8 and not inn:
            inn=True; start=peak.iloc[:t+1].idxmax() if False else wk.iloc[:t+1].idxmax()
        if inn and v[t]>=peak.iloc[t-1] and t>0 and v[t]>=wk.loc[start]:
            eps.append((start.date(), idx[t].date(), (idx[t]-start).days/365)); inn=False
    print("  回檔>20%的修復", eps, "仍未修復" if inn else "")
    # missing best days (daily part)
    rd = daily.pct_change().dropna()
    tot = (1+rd).prod()-1
    best = rd.sort_values(ascending=False)
    yrs=(daily.index[-1]-daily.index[0]).days/365
    for k in (5,10,20):
        m=(1+rd.drop(best.index[:k])).prod()-1
        print(f"  日線 {daily.index[0].date()}起 {yrs:.1f}年 全程 {tot:+.0%} 錯過最好 {k} 天 {m:+.0%}")
    worst = rd.sort_values().index[:10]
    near = sum(any(abs((b-w).days)<=14 for w in worst) for b in best.index[:10])
    print(f"  最好10天中 {near} 天距離最差10天兩週內")
    # buy after 10% / 20% drawdown from 52w high: forward 1y
    hi = wk.rolling(52,min_periods=26).max(); f1=wk.shift(-52)/wk-1
    base=f1.dropna()
    for th in (0.1,0.2):
        m_=(wk/hi-1<=-th)&f1.notna()
        print(f"  距一年高點 -{th:.0%}以上時買進 1年後上漲 {(f1[m_]>0).mean():.0%} 中位 {f1[m_].median():+.0%} n週={m_.sum()}  ｜全部 {(base>0).mean():.0%} {base.median():+.0%}")
    # lump sum vs 12-month DCA (12 monthly) compare value after 12 months
    mo = full.resample("ME").last().dropna()
    wins=[]; diffs=[]
    for t in range(len(mo)-12):
        p=mo.iloc[t:t+13].values
        lump=p[12]/p[0]; dca=np.mean(p[12]/p[0:12])
        wins.append(lump>dca); diffs.append(lump-dca)
    print(f"  一次投入 vs 12個月定期定額（看第12月底）：一次投入勝 {np.mean(wins):.0%}  n={len(wins)}  一次投入落後時中位差 {np.median([d for d in diffs if d<0]):+.1%}")
# leveraged ETF drag
lev,_=load("00631L_TW"); tw,_=load("TWII")
for a,b in [("2021-11-08","2026-11-19"),("2022-01-03","2022-12-30")]:
    l=lev.loc[a:b]; t=tw.loc[l.index[0]:l.index[-1]]
    print(f"\n00631L {l.index[0].date()}..{l.index[-1].date()} 漲跌 {l.iloc[-1]/l.iloc[0]-1:+.0%}  TWII {t.iloc[-1]/t.iloc[0]-1:+.0%}  2倍TWII {2*(t.iloc[-1]/t.iloc[0]-1):+.0%}")
# small edge: binomial
for p in (0.53,0.55):
    for n in (10,20,50,100):
        lose=sum(comb(n,k)*p**k*(1-p)**(n-k) for k in range(0,(n+1)//2 + (0 if n%2 else 1)) if k*2<=n) # wins <= half
        print(f"  勝率{p:.0%} 做{n}次 贏的次數不超過一半的機率 {lose:.0%}")
