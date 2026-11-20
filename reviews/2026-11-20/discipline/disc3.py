import json, pandas as pd
import pathlib; C=str(pathlib.Path(__file__).resolve().parents[3] / "docs/data/charts") + "/"
def load(f):
    d=json.load(open(C+f+".json"))
    w=pd.Series({pd.Timestamp(r[0]):r[4] for r in d["w"]}); dd=pd.Series({pd.Timestamp(r[0]):r[4] for r in d["d"]})
    return pd.concat([w[w.index<dd.index[0]], dd]).sort_index().resample("W-FRI").last().dropna()
for name in ("TWII","SPY"):
    s=load(name); peak=s.cummax(); dd=s/peak-1; inn=False
    for t in s.index:
        if not inn and dd[t]<=-0.2:
            inn=True; p0=peak[t]; fut=s[t:]; after=fut[fut.index<=t+pd.Timedelta(days=370)]
            rec=fut[fut>=p0]
            low=fut[: rec.index[0]] if len(rec) else fut
            y1=after.iloc[-1]/s[t]-1 if (s.index[-1]-t).days>360 else None
            print(name, "首次 -20%:", t.date(), f"之後再跌 {low.min()/s[t]-1:+.0%}（{low.idxmin().date()}）", f"1年後 {y1:+.0%}" if y1 is not None else "1年後 未到", "回到前高", rec.index[0].date() if len(rec) else "未回到")
        if inn and dd[t]==0: inn=False
