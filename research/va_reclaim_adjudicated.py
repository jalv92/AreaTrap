#!/usr/bin/env python3
"""Adjudicated VAL-break/reclaim measurement: A and B reconciled.

Conventions settled against val_reclaim_measure_A.py / va_reclaim_B.py:
  E_tick       strictly AFTER the break bar        (A) - B lets it fire on the break bar
  break_low    strictly BEFORE an intrabar fill    (A) - B includes the fill bar low
  resolution   INCLUDES the bar of an intrabar fill (B) - A starts at fill+1
               (bar-close entry still resolves from ic+1: that bar is spent)
  E_close      truncated final 30s slot rejected   (A)
  mark-out     unresolved -> last traded close     (B) - A marks at the entry price
Same-bar stop+target: stop wins (both agreed); measured to affect 0 trades.
"""
import sys, json
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

NPZ="/home/javlo/Code Projects/main-project/projects/Trading/NQData/NQ_continuous_1s.npz"
TICK=0.25; PT=20.0; COMM=5.76; VA=0.70; BUF=1.00; SLIP=2*TICK
O=9*3600+1800; C=16*3600

def load():
    d=np.load(NPZ,allow_pickle=True); ts=d["ts"].astype("int64")
    idx=pd.DatetimeIndex(pd.to_datetime(ts,unit="s",utc=True)).tz_convert(ZoneInfo("America/New_York"))
    sod=np.asarray(idx.hour*3600+idx.minute*60+idx.second)
    m=(sod>=O)&(sod<C)
    return dict(ts=ts[m],sod=sod[m],day=np.asarray(idx.strftime("%Y-%m-%d"))[m],
        high=d["high"][m].astype(np.float64),low=d["low"][m].astype(np.float64),
        close=d["close"][m].astype(np.float64),volume=d["volume"][m].astype(np.float64))

def value_area(low,high,vol):
    li=np.rint(low/TICK).astype(np.int64); hi=np.rint(high/TICK).astype(np.int64)
    per=vol/(hi-li+1); base,top=li.min(),hi.max()
    dd=np.zeros(top-base+2); np.add.at(dd,li-base,per); np.add.at(dd,hi-base+1,-per)
    h0=np.cumsum(dd)[:-1]; keep=np.nonzero(h0>1e-9)[0]; h=h0[keep]
    need=VA*h.sum(); p=int(h.argmax()); a=b=p; acc=h[p]
    while acc<need and (a>0 or b<len(h)-1):
        u=h[b+1] if b<len(h)-1 else -1.0; dn=h[a-1] if a>0 else -1.0
        if u>=dn: b+=1; acc+=u
        else: a-=1; acc+=dn
    px=lambda k: float((keep[k]+base)*TICK)
    return px(a),px(b),px(p)

def resolve(L,H,Cl,start,stop,tgt):
    """returns exit_px, exit_i, kind, ambiguous(bool)"""
    if start>=len(L): return Cl[-1],len(L)-1,"unresolved",False
    s=np.nonzero(L[start:]<=stop)[0]; t=np.nonzero(H[start:]>=tgt)[0]
    si=start+int(s[0]) if len(s) else None; ti=start+int(t[0]) if len(t) else None
    amb = (si is not None and ti is not None and si==ti)
    if si is not None and (ti is None or si<=ti): return stop-SLIP,si,"stop",amb
    if ti is not None: return tgt,ti,"target",amb
    return Cl[-1],len(L)-1,"unresolved",False

def pnl(entry,ex): return (ex-entry)*PT-COMM

def scan(W,D):
    ts,sod,day=D["ts"],D["sod"],D["day"]
    hi,lo,cl,vol=D["high"],D["low"],D["close"],D["volume"]
    bnd=np.nonzero(np.r_[True,day[1:]!=day[:-1],True])[0]; Ws=W*60
    ev=[]; nwin10=0; nwin60=0; ndegen=0
    for b in range(len(bnd)-1):
        a,z=bnd[b],bnd[b+1]
        ssod,sts=sod[a:z],ts[a:z]; sh,sl,sc,sv=hi[a:z],lo[a:z],cl[a:z],vol[a:z]
        for k in range((C-O)//Ws):
            t0=O+k*Ws
            bs,be=np.searchsorted(ssod,[t0,t0+Ws])
            nb=be-bs
            if nb<10: continue
            nwin10+=1
            if nb>=60: nwin60+=1
            val,vah,poc=value_area(sl[bs:be],sh[bs:be],sv[bs:be])
            degen = (vah<=val)
            if degen: ndegen+=1
            ae=np.searchsorted(ssod,t0+2*Ws)
            L,H,Cl,T=sl[be:ae],sh[be:ae],sc[be:ae],sts[be:ae]
            if len(L)<5: continue
            blw=np.nonzero(L<val)[0]
            if not len(blw): continue
            fb=int(blw[0])
            eB=np.nonzero(H[fb:]>=val+TICK)[0];   itB=fb+int(eB[0]) if len(eB) else None
            eA=np.nonzero(H[fb+1:]>=val+TICK)[0]; itA=fb+1+int(eA[0]) if len(eA) else None
            g=T//30; gs=np.nonzero(np.r_[True,g[1:]!=g[:-1]])[0]; ge=np.r_[gs[1:]-1,len(g)-1]
            icB=None; icA=None; a_done=False
            t_open_ep=int(sts[0])-(int(ssod[0])-O)
            lim_ts=min(t_open_ep+(t0+2*Ws-O),int(sts[-1])+1)
            for s_,e_ in zip(gs,ge):
                slot_end=(int(g[s_])+1)*30
                if icB is None and e_>=fb and Cl[e_]>val: icB=int(e_)
                if not a_done:
                    if slot_end>lim_ts: a_done=True
                    elif slot_end>int(T[fb]) and Cl[e_]>val: icA=int(e_); a_done=True
                if icB is not None and a_done: break
            ev.append(dict(val=val,vah=vah,fb=fb,itA=itA,itB=itB,icA=icA,icB=icB,
                           nb=nb,degen=degen,L=L,H=H,C=Cl,day=day[a],t0=t0,
                           lastslot_trunc=None))
    return ev,nwin10,nwin60,ndegen


import numpy as np
def agg(pn):
    a=np.asarray(pn,float)
    if not a.size: return "n=0"
    w,l=a[a>0],a[a<=0]
    pf=float(w.sum()/abs(l.sum())) if l.sum()!=0 else 999.
    return dict(n=int(a.size),win=round(float((a>0).mean()),4),avg=round(float(a.mean()),2),
                pf=round(pf,3),worst=round(float(a.min()),2),
                medloss=round(float(np.median(-l)),2) if len(l) else 0.0,
                p90loss=round(float(np.percentile(-l,90)),2) if len(l) else 0.0)
D=load()
OUT={}
for W in (10,30):
    ev,n10,n60,ndeg=scan(W,D)
    R={k:[] for k in ("CLOSE","CLOSE1T","SM_ALL","SM_SUB","SM_TICKONLY","LIM_T","LIM_TH")}
    U={k:0 for k in R}; MISS={"LIM_T":0,"LIM_TH":0}; ATT=0; RT=[0,0]; degen_marks=0
    for e in ev:
        L,H,Cl,val,vah,fb=e['L'],e['H'],e['C'],e['val'],e['vah'],e['fb']
        it,ic=e['itA'],e['icA']
        if it is not None:                      # ADJUDICATED STOPMKT
            stop=float(L[fb:it].min())-BUF; ent=val+TICK+SLIP
            xp,xi,kd,_=resolve(L,H,Cl,it,stop,vah); p=pnl(ent,xp)
            R["SM_ALL"].append(p); U["SM_ALL"]+= (kd=="unresolved")
            if ic is not None: R["SM_SUB"].append(p); U["SM_SUB"]+=(kd=="unresolved")
            else: R["SM_TICKONLY"].append(p); U["SM_TICKONLY"]+=(kd=="unresolved")
        if ic is None: continue
        ATT+=1
        stop=float(L[fb:ic+1].min())-BUF; ent=float(Cl[ic])
        xp,xi,kd,_=resolve(L,H,Cl,ic+1,stop,vah)
        R["CLOSE"].append(pnl(ent,xp)); U["CLOSE"]+=(kd=="unresolved")
        R["CLOSE1T"].append(pnl(ent+TICK,xp)); U["CLOSE1T"]+=(kd=="unresolved")
        if ic+1>=len(L): degen_marks+=1
        seg=L[ic+1:xi+1]; RT[1]+=1; RT[0]+= (len(seg)>0 and seg.min()<=val)
        for key,lim in (("LIM_T",val),("LIM_TH",val-TICK)):
            hits=np.nonzero(L[ic+1:xi+1]<=lim)[0]
            if not len(hits): MISS[key]+=1; continue
            f=ic+1+int(hits[0])
            fx,_,fk,_=resolve(L,H,Cl,f,stop,vah)      # fill bar INCLUSIVE
            R[key].append(pnl(val,fx)); U[key]+=(fk=="unresolved")
    print(f"\n########## ADJUDICATED  W={W}   sessions=439  windows(build>=60 bars)={n60}")
    print(f"  E_close attempts {ATT} | retest {RT[0]}/{RT[1]} = {RT[0]/RT[1]:.1%} | "
          f"trades whose E_close bar is the last arm bar (marked flat): {degen_marks}")
    for k in ("CLOSE","CLOSE1T","SM_ALL","SM_SUB","SM_TICKONLY","LIM_T","LIM_TH"):
        s=agg(R[k]); extra=f" miss={MISS[k]}/{ATT}" if k in MISS else ""
        print(f"  {k:<12} n={s['n']:<6} win={s['win']:.4f} avg=${s['avg']:>8.2f} pf={s['pf']:.3f} "
              f"worst=${s['worst']:>9.2f} medLoss=${s['medloss']:>7.2f} p90Loss=${s['p90loss']:>7.2f} "
              f"unres={U[k]}{extra}")
    OUT[W]={k:agg(v) for k,v in R.items()}
