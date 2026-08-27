# -*- coding: utf-8 -*-
"""
asr_service.py — 实时流式语音转写服务(本机·不上云)
用独立 asr_venv 的 python 跑(faster-whisper / soundcard / webrtcvad / zhconv)。

管线:soundcard 采集(16k) → webrtcvad 流式断句 → 每句存 wav 原声 → faster-whisper 转录
     (带领域提示词)→ 繁转简 → 写 JSONL(含原声 wav 引用)。
被 WebUI(8799)以子进程拉起/停止;结果实时追加到 --out 的 JSONL(每行 audio 字段=原声片段),
WebUI tail 推浏览器 + 每行 ▶ 回放原声。

音频源:
  --source loopback   抓【系统声音】(扬声器回环)—— 线上会议听对方说话(默认)
  --source mic        抓【麦克风】—— 现场/自己说话
  --name <子串>       指定设备名子串(loopback=扬声器名 / mic=麦克风名)

用法:
  asr_venv/python asr_service.py --model <目录> --out <jsonl>
      [--source loopback|mic] [--name <子串>] [--status <json>] [--prompt <领域提示>]
      [--compute-type int8] [--lang zh] [--max-sec 12] [--silence-ms 500] [--aggr 2] [--no-audio]
"""
import argparse, collections, json, os, re, sys, time, threading, signal, queue, wave
import numpy as np

_REPEAT_RE = re.compile(r'(.)\1{3,}')   # 单字符连续重复≥4次 = whisper 静音重复幻觉

SR = 16000
FRAME_MS = 30
FRAME_LEN = SR * FRAME_MS // 1000   # 480 采样/帧


def f32_to_pcm16(x):
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype('<i2')


def write_wav(path, audio_f32, sr=SR):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(f32_to_pcm16(audio_f32).tobytes())


def pick_source(sc, source, name):
    if source == 'loopback':
        spk = None
        if name:
            for s in sc.all_speakers():
                if name.lower() in s.name.lower():
                    spk = s; break
        spk = spk or sc.default_speaker()
        return sc.get_microphone(id=str(spk.name), include_loopback=True), spk.name
    else:
        m = None
        if name:
            for mm in sc.all_microphones():
                if name.lower() in mm.name.lower():
                    m = mm; break
        m = m or sc.default_microphone()
        return m, m.name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--source', default='loopback', choices=['loopback', 'mic'])
    ap.add_argument('--name', default=None)
    ap.add_argument('--status', default=None)
    ap.add_argument('--hotwords', default=None)        # 领域热词(hotwords)偏置专名·不泄漏进输出
    ap.add_argument('--compute-type', default='int8')  # int8 / int8_float32 / float32
    ap.add_argument('--lang', default='zh')
    ap.add_argument('--max-sec', type=float, default=12.0)
    ap.add_argument('--silence-ms', type=int, default=500)
    ap.add_argument('--aggr', type=int, default=2)
    ap.add_argument('--beam', type=int, default=5)
    ap.add_argument('--no-audio', action='store_true')  # 不保存原声
    args = ap.parse_args()

    import soundcard as sc
    import webrtcvad
    from faster_whisper import WhisperModel
    import zhconv

    stop = threading.Event()

    # 原声片段目录:由 out 派生(session-<ts>.jsonl → session-<ts>.clips/)
    save_audio = not args.no_audio
    clips_dir = None
    if save_audio:
        base = os.path.splitext(args.out)[0]
        clips_dir = base + ".clips"
        os.makedirs(clips_dir, exist_ok=True)
    clips_rel = os.path.basename(clips_dir) if clips_dir else None

    def write_status(state, **extra):
        if not args.status:
            return
        d = {'state': state, 'ts': time.time(), 'pid': os.getpid()}
        d.update(extra)
        tmp = args.status + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, args.status)

    def write_line(obj):
        with open(args.out, 'a', encoding='utf-8') as f:
            f.write(json.dumps(obj, ensure_ascii=False) + '\n')
            f.flush()

    write_status('loading', model=os.path.basename(args.model.rstrip('/\\')))
    model = WhisperModel(args.model, device='cpu', compute_type=args.compute_type, cpu_threads=6)
    vad = webrtcvad.Vad(args.aggr)
    # vad_filter(faster-whisper 内置 silero)需 onnxruntime;本机 onnxruntime DLL 常坏 →
    # 优雅降级:能 import 才开;不能就只靠 webrtcvad 断句(已挡静音)+ 幻觉黑名单+重复抑制。
    try:
        import onnxruntime  # noqa: F401
        _use_vadfilter = True
    except Exception:
        _use_vadfilter = False

    # 转录 worker(与采集解耦)
    seg_q = queue.Queue()
    _last = {"text": None, "n": 0}
    # 常见中文幻觉短语(whisper 在静音/非语音上瞎编·硬过滤兜底)
    HALLUC = ("字幕by", "字幕製作", "請不吝", "请不吝", "点赞", "點贊", "订阅", "訂閱",
              "明镜与点点", "感謝觀看", "感谢观看", "谢谢观看", "谢谢大家收看")

    def transcriber():
        while not (stop.is_set() and seg_q.empty()):
            try:
                item = seg_q.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                break
            seq, audio = item
            # 先存原声(转录失败也留声)
            audio_rel = None
            if save_audio:
                fn = "seg-%04d.wav" % seq
                try:
                    write_wav(os.path.join(clips_dir, fn), audio)
                    audio_rel = clips_rel + "/" + fn
                except Exception:
                    audio_rel = None
            t0 = time.time()
            # hotwords=领域词偏置(不泄漏进输出·替代会泄漏的 initial_prompt)
            # vad_filter(有 onnxruntime 才开)+ no_speech 阈值 → 防非语音幻觉
            segs, _ = model.transcribe(audio, language=args.lang, beam_size=args.beam,
                                       hotwords=(args.hotwords or None),
                                       initial_prompt=None,
                                       condition_on_previous_text=False,
                                       vad_filter=_use_vadfilter,
                                       no_speech_threshold=0.6,
                                       repetition_penalty=1.15,
                                       no_repeat_ngram_size=3)
            text = ''.join(s.text for s in segs).strip()
            text = zhconv.convert(text, 'zh-cn')
            # 幻觉抑制:重复字符/已知瞎编短语/与上句完全重复 → 丢弃文本(仍留原声)
            drop = False
            if text:
                if _REPEAT_RE.search(text):     # 连续重复字符(作证证证证)=静音重复幻觉
                    drop = True
                elif any(h in text for h in HALLUC):
                    drop = True
                elif text == _last["text"]:
                    _last["n"] += 1
                    if _last["n"] >= 1:   # 连续重复即抑制(whisper 静音循环)
                        drop = True
                else:
                    _last["text"] = text
                    _last["n"] = 0
            out_text = "" if drop else text
            dur = len(audio) / SR
            if out_text or audio_rel:
                write_line({'ts': time.time(), 'seq': seq, 'text': out_text,
                            'dur': round(dur, 2), 'audio': audio_rel,
                            'rtf': round((time.time() - t0) / max(dur, 0.01), 3)})

    tr = threading.Thread(target=transcriber, daemon=True)
    tr.start()

    def handle_sig(*_):
        stop.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, handle_sig)
        except Exception:
            pass

    pad_frames = max(1, args.silence_ms // FRAME_MS)
    ring = collections.deque(maxlen=pad_frames)
    triggered = False
    voiced = []
    seq = [0]
    max_frames = int(SR * args.max_sec / FRAME_LEN)

    def flush_voiced():
        nonlocal voiced
        if voiced:
            audio = np.concatenate(voiced)
            voiced = []
            if len(audio) > SR * 0.3:
                seq[0] += 1
                seg_q.put((seq[0], audio))

    try:
        rec_obj, dev_name = pick_source(sc, args.source, args.name)
    except Exception as e:
        write_status('error', msg='选设备失败: %s' % e)
        print('pick_source err:', e, file=sys.stderr)
        return

    write_status('listening', source=args.source, device_name=str(dev_name),
                 model=os.path.basename(args.model.rstrip('/\\')), compute_type=args.compute_type,
                 save_audio=save_audio, vad_filter=_use_vadfilter)
    try:
        with rec_obj.recorder(samplerate=SR, channels=1) as rec:
            while not stop.is_set():
                data = rec.record(numframes=FRAME_LEN)
                frame = data[:, 0] if data.ndim > 1 else data
                if len(frame) < FRAME_LEN:
                    frame = np.pad(frame, (0, FRAME_LEN - len(frame)))
                is_speech = vad.is_speech(f32_to_pcm16(frame).tobytes(), SR)
                if not triggered:
                    ring.append((frame, is_speech))
                    if sum(1 for _, sp in ring if sp) > 0.9 * ring.maxlen:
                        triggered = True
                        voiced.extend(f for f, _ in ring)
                        ring.clear()
                else:
                    voiced.append(frame)
                    ring.append((frame, is_speech))
                    if sum(1 for _, sp in ring if not sp) > 0.9 * ring.maxlen:
                        triggered = False
                        flush_voiced()
                        ring.clear()
                    elif len(voiced) > max_frames:
                        flush_voiced()
    except Exception as e:
        write_status('error', msg=str(e))
        print('capture error:', e, file=sys.stderr)

    flush_voiced()
    stop.set()
    seg_q.put(None)
    tr.join(timeout=30)
    write_status('stopped')


if __name__ == '__main__':
    main()
