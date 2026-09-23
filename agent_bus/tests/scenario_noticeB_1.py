# -*- coding: utf-8 -*-
"""档B 场景测试①(真路径·真格式):fyi免回执/--receipt/索取语软警示/缺省兼容/debounce=0/去抖合并。由 bus_smoke 调用·期待末行 ALL PASS。"""
import os, sys, json, time, subprocess, shutil, glob, threading
os.environ["PYTHONUTF8"]="1"
BUS=os.path.expanduser(r"~\.claude\agent_bus"); AB=[r"C:\anaconda3\envs\server\python.exe", r"C:\apphome\agent_fleet\agent_bus\agentbus.py"]
R="__smoke__"; D=os.path.join(BUS,R)
def ab(*a, env=None, timeout=60):
    e=dict(os.environ); e.update(env or {})
    return subprocess.run(AB+list(a), capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=timeout)
def clean():
    for sub in ("inbox","processing","done"):
        for f in glob.glob(os.path.join(D,sub,"*.json")): os.remove(f)
def notice(i, age_s=0):
    nid="notice-1790000000%03d-%s-t%03d"%(i,R,i)
    o={"id":nid,"from":R,"to":R,"kind":"notice","title":"✅ 完成:t%d"%i,"task":"[完成通知]","notice_of":nid[7:],"notice_status":"ok",
       "created":time.strftime("%Y-%m-%dT%H:%M:%S",time.localtime(time.time()-age_s)),"status":"pending"}
    p=os.path.join(D,"inbox",nid+".json"); json.dump(o,open(p,"w",encoding="utf-8"),ensure_ascii=False); return nid
NESTED=bool(os.environ.get("AGENTBUS_SMOKE_NESTED"))  # 被 bus_smoke 调用时:不注册/不注销测试角色(smoke setup/teardown 管)
if not NESTED: ab("register","--role",R,"--desc","smoke")
os.makedirs(os.path.join(D,"inbox"),exist_ok=True); clean()
ok=True
def chk(cond,msg):
    global ok; print(("PASS " if cond else "FAIL ")+msg); ok = ok and cond
# T1 L1
r=ab("send","--to",R,"--from",R,"--kind","fyi","--title","t","--task","知悉即可"); tid=r.stdout.strip().split()[-1]
o=json.load(open(os.path.join(D,"inbox",tid+".json"),encoding="utf-8")); chk(o.get("no_receipt") is True and o.get("kind")=="fyi","T1a fyi→no_receipt")
r=ab("send","--to",R,"--from",R,"--kind","fyi","--receipt","--title","t","--task","知悉"); tid=r.stdout.strip().split()[-1]
o=json.load(open(os.path.join(D,"inbox",tid+".json"),encoding="utf-8")); chk(not o.get("no_receipt") and o.get("receipt") is True,"T1b --receipt 覆盖")
r=ab("send","--to",R,"--from",R,"--kind","fyi","--title","t","--task","请确认这个口径是否合意?"); chk("索取语" in r.stderr,"T1c fyi索取语软警示")
r=ab("send","--to",R,"--from",R,"--title","t","--task","普通任务"); tid=r.stdout.strip().split()[-1]
o=json.load(open(os.path.join(D,"inbox",tid+".json"),encoding="utf-8")); chk("no_receipt" not in o and "kind" not in o,"T1d 缺省task不落字段(向后兼容)")
clean()
# T4 debounce=0 byte-identical:最老为纯回执→立即返回单条无coalesced
notice(1); notice(2)
t0=time.time(); r=ab("wait","--role",R,"--timeout","15",env={"AGENTBUS_NOTICE_DEBOUNCE_S":"0"}); dt=time.time()-t0
o=json.loads(r.stdout.strip().splitlines()[-1]); chk(o.get("kind")=="notice" and "coalesced" not in o and dt<6,"T4 debounce=0 立即领单条 (%.1fs)"%dt)
clean()
# T2 去抖合并:3 条纯回执·窗 4s → ~4s 后返回首条+coalesced 2·全部进 processing
n=[notice(i) for i in (1,2,3)]
t0=time.time(); r=ab("wait","--role",R,"--timeout","20",env={"AGENTBUS_NOTICE_DEBOUNCE_S":"4"}); dt=time.time()-t0
o=json.loads(r.stdout.strip().splitlines()[-1]); proc=len(glob.glob(os.path.join(D,"processing","*.json")))
chk(o.get("kind")=="notice" and len(o.get("coalesced") or [])==2 and proc==3 and 3.5<=dt<=12,"T2 去抖4s合并领3条 (%.1fs·processing=%d·coalesced=%s)"%(dt,proc,len(o.get("coalesced") or [])))
clean()
# teardown
if not NESTED:
    shutil.rmtree(D,ignore_errors=True)
    a=json.load(open(os.path.join(BUS,"agents.json"),encoding="utf-8")); a.pop(R,None)
    sys.path.insert(0,r"C:pphome\agent_fleetgent_bus"); from bus.io import _write_json; from pathlib import Path; _write_json(Path(BUS)/"agents.json",a)
else:
    clean()
print("teardown OK"); print("ALL PASS" if ok else "SOME FAIL")
