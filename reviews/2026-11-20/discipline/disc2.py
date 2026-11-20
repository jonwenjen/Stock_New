import json, pandas as pd
import pathlib; C=str(pathlib.Path(__file__).resolve().parents[3] / "docs/data/charts") + "/"
def daily(f): return pd.Series({pd.Timestamp(r[0]):r[4] for r in json.load(open(C+f+".json"))["d"]})
lev, tw, e50 = daily("00631L_TW"), daily("TWII"), daily("0050_TW")
df = pd.concat({"l":lev,"t":tw,"e":e50},axis=1).dropna()
for c in "lte": print(c, "maxDD", f"{(df[c]/df[c].cummax()-1).min():.0%}", "now from peak", f"{df[c].iloc[-1]/df[c].max()-1:+.1%}", df[c].idxmax().date())
# choppy windows: 120 trading days, 0050 change within ±3%
res=[]
for i in range(0,len(df)-120,5):
    w=df.iloc[i:i+121]; e=w.e.iloc[-1]/w.e.iloc[0]-1; l=w.l.iloc[-1]/w.l.iloc[0]-1
    if abs(e)<=0.03: res.append((w.index[0].date(),w.index[-1].date(),e,l))
print(len(res)); 
import statistics
print("choppy: median 00631L", f"{statistics.median(r[3] for r in res):+.1%}", "worst", min(res,key=lambda r:r[3]))
print("share 00631L < 2x0050:", sum(r[3] < 2*r[2] for r in res)/len(res))
# recovery need
for d in (0.1,0.2,0.3,0.5): print(d, f"{1/(1-d)-1:.0%}")
