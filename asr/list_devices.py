# -*- coding: utf-8 -*-
"""list_devices.py — 枚举可用音频源(用 asr_venv python 跑,输出 JSON)。
WebUI /api/asr/devices 调它;因 soundcard 只在 asr_venv 里。
输出: {"speakers":[{name}], "mics":[{name}], "default_speaker":..., "default_mic":...}
"""
import json, sys
try:
    import soundcard as sc
    out = {
        "speakers": [{"name": s.name} for s in sc.all_speakers()],
        "mics": [{"name": m.name} for m in sc.all_microphones()],
    }
    try:
        out["default_speaker"] = sc.default_speaker().name
    except Exception:
        out["default_speaker"] = None
    try:
        out["default_mic"] = sc.default_microphone().name
    except Exception:
        out["default_mic"] = None
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
except Exception as e:
    sys.stdout.write(json.dumps({"error": str(e)}, ensure_ascii=False))
