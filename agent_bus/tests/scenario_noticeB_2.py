# -*- coding: utf-8 -*-
"""档B 场景测试②(真路径):去抖窗内非回执零延迟(免jitter)/回执留inbox/armed窗内守卫不误判。由 bus_smoke 调用·期待末行 ALL PASS。"""
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
    json.dump(o,open(os.path.join(D,"inbox",nid+".json"),"w",encoding="utf-8"),ensure_ascii=False); return nid
NESTED=bool(os.environ.get("AGENTBUS_SMOKE_NESTED"))  # 被 bus_smoke 调用时:不注册/不注销测试角色(smoke setup/teardown 管)
if not NESTED: ab("register","--role",R,"--desc","smoke")
os.makedirs(os.path.join(D,"inbox"),exist_ok=True); clean()
ok=True
def chk(c,m):
    global ok; print(("PASS " if c else "FAIL ")+m); ok=ok and c
# T3 窗内非回执零延迟(send 免 jitter·测的是去抖不是错峰)
notice(1)
def later():
    time.sleep(1.5); ab("send","--to",R,"--from",R,"--title","urgent-task","--task","真任务",env={"AGENTBUS_NO_JITTER":"1"})
threading.Thread(target=later,daemon=True).start()
t0=time.time(); r=ab("wait","--role",R,"--timeout","20",env={"AGENTBUS_NOTICE_DEBOUNCE_S":"10"}); dt=time.time()-t0
o=json.loads(r.stdout.strip().splitlines()[-1]); chk(o.get("title")=="urgent-task" and dt<6,"T3 窗内任务即时fire (%.1fs·got=%s)"%(dt,o.get("title")))
# 回执仍在 inbox 未被领(任务优先·回执留给下一窗)
chk(len(glob.glob(os.path.join(D,"inbox","notice-*.json")))==1,"T3b 回执未被抢领·留inbox")
clean()
# T5 守卫不误判:armed wait(窗60s)+老回执 → reg 在册/_self_wake_state=armed;guard dry-run 不列
notice(1, age_s=3600)
p=subprocess.Popen(AB+["wait","--role",R,"--timeout","25"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=dict(os.environ,AGENTBUS_NOTICE_DEBOUNCE_S="60"))
time.sleep(3)
sys.path.insert(0,r"C:\apphome\agent_fleet\agent_bus")
from bus.selfwake import _self_wake_state
st=_self_wake_state(R); g=ab("selfwake-guard","--dry-run").stdout
reg=os.path.exists(os.path.join(D,"wait.reg.json"))
chk(st[0]=="armed" and reg and (R not in g),"T5 去抖窗内 armed(%s·reg=%s)·守卫不催"%(st[0],reg))
p.kill(); time.sleep(0.5); clean()
if not NESTED:
    shutil.rmtree(D,ignore_errors=True)
    a=json.load(open(os.path.join(BUS,"agents.json"),encoding="utf-8")); a.pop(R,None)
    sys.path.insert(0,r"C:pphome\agent_fleetgent_bus"); from bus.io import _write_json; from pathlib import Path; _write_json(Path(BUS)/"agents.json",a)
else:
    clean()
print("teardown OK"); print("ALL PASS" if ok else "SOME FAIL")
