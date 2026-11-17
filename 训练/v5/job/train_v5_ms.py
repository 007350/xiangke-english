# xiangke LoRA v5 (same hparams as v4, data ds_v5) -- ModelScope PAI-DSW (A10 24GB) port of nb_lora_v4.py
# Same data / LoRA hparams / 3 epochs / eval metrics as the Kaggle v4 notebook.
# Differences: bf16 training (no GradScaler), resumable checkpoints, eval fully resumable,
# periodic upload of outputs to the private ModelScope repo (token from env MODELSCOPE_API_TOKEN only).
import os, sys, json, time, glob, math, random, shutil, collections, argparse, threading, traceback, datetime

ap = argparse.ArgumentParser()
ap.add_argument("--job", default=os.path.dirname(os.path.abspath(__file__)))
ap.add_argument("--out", default="/mnt/workspace/out_v5")
ap.add_argument("--model-dir", default="/mnt/workspace/models/Spark-X2.5-1.7B")
ap.add_argument("--base-id", default="XHToken/Spark-X2.5-1.7B")
ap.add_argument("--repo", default="xiaobang123/xiangke-lora-job-v5")
ap.add_argument("--sync-prefix", default="outputs")
ap.add_argument("--sync-min", type=float, default=25.0)   # periodic upload interval
ap.add_argument("--ckpt-min", type=float, default=30.0)   # periodic resumable checkpoint interval
ap.add_argument("--skip-base-eval", action="store_true", default=os.environ.get("SKIP_BASE", "0") == "1")
ap.add_argument("--epochs", type=int, default=3)
ap.add_argument("--acc", type=int, default=16)
ap.add_argument("--warm", type=int, default=15)
ap.add_argument("--grad-ckpt", default="auto", choices=["auto", "on", "off"])
ap.add_argument("--eval-dtype", default="fp16", choices=["fp16", "bf16"])  # fp16 = same numerics as Kaggle v4 eval
ap.add_argument("--train-budget-h", type=float, default=9.2)  # script-time; 10h session -> pause + resume on rerun
ap.add_argument("--eval-stop-h", type=float, default=9.6)
# dry-run / test options
ap.add_argument("--tiny-model", action="store_true")
ap.add_argument("--tok-dir", default=None)
ap.add_argument("--limit-train", type=int, default=0)
ap.add_argument("--limit-eval", type=int, default=0)
ap.add_argument("--maxnew", type=int, default=512)
ap.add_argument("--held-loss-n", type=int, default=100)
ap.add_argument("--stop-after-steps", type=int, default=0)
ap.add_argument("--stop-after-epochs", type=int, default=0)
ap.add_argument("--no-sync", action="store_true")
A = ap.parse_args()

T_START = time.time()
el = lambda: (time.time() - T_START) / 3600
OUT = A.out; os.makedirs(OUT, exist_ok=True); os.chdir(OUT)
P = lambda *f: os.path.join(OUT, *f)
for m in ["ERROR", "PAUSED"]:
    if os.path.exists(P(m)): os.remove(P(m))
if os.path.exists(P("DONE")):
    print("DONE marker exists in", OUT, "- nothing to do"); sys.exit(0)

def now(): return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S UTC+8")
def jdump(o, fn):
    tmp = fn + ".tmp"; json.dump(o, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, fn)
def jload(fn, d=None):
    try: return json.load(open(fn))
    except Exception: return d

PROG = jload(P("progress.json"), {}) or {}
PROG.setdefault("runs", 0); PROG["runs"] += 1; PROG["run_started"] = now()
def prog(**kw):
    PROG.update(kw); PROG["updated"] = now(); PROG["run_elapsed_h"] = round(el(), 3); jdump(PROG, P("progress.json"))

# ---------------- ModelScope sync (background) ----------------
LOCK = threading.RLock()
TOKEN = os.environ.get("MODELSCOPE_API_TOKEN", "").strip()
SYNC_ON = bool(TOKEN) and not A.no_sync
_api = None; _manifest = jload(P(".sync_manifest.json"), {}) or {}
SYNC_SKIP = {".sync_manifest.json", "progress.json.tmp", "metrics.json.tmp"}
def _api_get():
    global _api
    if _api is None:
        from modelscope.hub.api import HubApi
        _api = HubApi(); _api.login(TOKEN)
    return _api
_SYNC_SER = threading.Lock()
def sync(reason=""):
    if not SYNC_ON: return
    with _SYNC_SER: _sync(reason)
def _sync(reason):
    stage = P(".sync_stage")
    with LOCK:
        shutil.rmtree(stage, ignore_errors=True); changed = {}
        for root, dirs, files in os.walk(OUT):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith(".tmp")]
            for f in files:
                if f in SYNC_SKIP or f.endswith(".tmp"): continue
                fp = os.path.join(root, f); rel = os.path.relpath(fp, OUT); st = os.stat(fp)
                sig = [st.st_size, st.st_mtime_ns]
                if _manifest.get(rel) != sig:
                    dst = os.path.join(stage, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(fp, dst); changed[rel] = sig
    gone = [rel for rel in list(_manifest) if not os.path.exists(P(rel))]  # e.g. stale PAUSED / ERROR markers
    if gone:
        try:
            _api_get().delete_files(A.repo, file_paths=[f"{A.sync_prefix}/{g}" for g in gone], commit_message=f"remove stale {gone[:5]}")
            with LOCK:
                for g in gone: _manifest.pop(g, None)
                jdump(_manifest, P(".sync_manifest.json"))
            print(f"[sync] removed stale remote files: {gone[:10]}", flush=True)
        except Exception as e:
            print(f"[sync] remote delete failed: {type(e).__name__}: {str(e)[:200]}", flush=True)
    if not changed: return
    t0 = time.time()
    for attempt in range(3):
        try:
            _api_get().upload_folder(repo_id=A.repo, folder_path=stage, path_in_repo=A.sync_prefix, repo_type="model",
                                     commit_message=f"sync {reason} {now()} ({len(changed)} files)", disable_tqdm=True)
            with LOCK:
                _manifest.update(changed); jdump(_manifest, P(".sync_manifest.json"))
            print(f"[sync] {reason}: uploaded {len(changed)} files in {time.time()-t0:.0f}s", flush=True); break
        except Exception as e:
            print(f"[sync] {reason}: attempt {attempt+1} failed: {type(e).__name__}: {str(e)[:300]}", flush=True); time.sleep(20)
    shutil.rmtree(stage, ignore_errors=True)
def _sync_loop():
    while True:
        time.sleep(A.sync_min * 60)
        try: sync("periodic")
        except Exception as e: print("[sync] loop error", type(e).__name__, str(e)[:200], flush=True)
if SYNC_ON: threading.Thread(target=_sync_loop, daemon=True).start()
else: print("[sync] disabled (no MODELSCOPE_API_TOKEN or --no-sync)", flush=True)

def main():
    import torch, torch.nn.functional as F
    import transformers, peft
    from transformers import AutoTokenizer, AutoModelForCausalLM, AutoConfig, GenerationConfig
    from peft import LoraConfig, get_peft_model, PeftModel, set_peft_model_state_dict, get_peft_model_state_dict
    CUDA = torch.cuda.is_available(); DEV = "cuda" if CUDA else "cpu"
    BF16 = CUDA and torch.cuda.is_bf16_supported()
    TR_DT = torch.bfloat16 if BF16 else (torch.float16 if CUDA else torch.float32)
    EV_DT = (torch.float16 if A.eval_dtype == "fp16" else torch.bfloat16) if CUDA else torch.float32
    print("transformers", transformers.__version__, "torch", torch.__version__, "peft", peft.__version__,
          torch.cuda.get_device_name(0) if CUDA else "CPU", "train_dtype", TR_DT, "eval_dtype", EV_DT, flush=True)
    sys.path.insert(0, A.job)
    import kernel, eval_core as E
    src = os.path.join(A.job, "ds_v5")

    # ---------- base model (ModelScope) ----------
    prog(phase="download_model")
    if A.tiny_model:
        path = A.tok_dir
    else:
        if not os.path.exists(os.path.join(A.model_dir, ".complete")):
            from modelscope import snapshot_download
            snapshot_download(A.base_id, local_dir=A.model_dir, allow_patterns=["*.json", "*.py", "*.safetensors", "*.jinja", "*.txt"])
            open(os.path.join(A.model_dir, ".complete"), "w").write(now())
        path = A.model_dir
    tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
    if os.path.exists(os.path.join(path, "chat_template.jinja")) and not tok.chat_template:
        tok.chat_template = open(os.path.join(path, "chat_template.jinja")).read()
    EOS = 1
    print("eos token:", repr(tok.convert_ids_to_tokens(EOS)), flush=True)
    GC = GenerationConfig(bos_token_id=0, eos_token_id=1, pad_token_id=2, do_sample=False)
    def load_model(dt):
        if A.tiny_model:
            cfg = AutoConfig.from_pretrained(path, trust_remote_code=True)
            cfg.num_hidden_layers = 4; cfg.layer_types = cfg.layer_types[:4]; cfg.hidden_size = 64; cfg.intermediate_size = 128
            cfg.num_attention_heads = 2; cfg.num_key_value_heads = 1; cfg.head_dim = 32
            torch.manual_seed(0); m = AutoModelForCausalLM.from_config(cfg, trust_remote_code=True).to(dt)
        else:
            m = AutoModelForCausalLM.from_pretrained(path, trust_remote_code=True, dtype=dt)
        m = m.to(DEV); m.generation_config = GC; return m

    load = lambda fn: [json.loads(l) for l in open(os.path.join(src, fn))]
    train, held, ext = load("train.jsonl"), load("heldout.jsonl"), load("external_test.jsonl")
    evset = json.load(open(os.path.join(A.job, "eval_set.json"))); evq = {q["id"]: q for q in evset["questions"]}
    held_t = [r for r in held if r["id"].startswith("xk3-")]; held = [r for r in held if not r["id"].startswith("xk3-")]
    if A.limit_train: train = train[:A.limit_train]
    if A.limit_eval: ext = ext[:A.limit_eval]; held_t = held_t[:A.limit_eval]
    print("train", len(train), "heldout_v2", len(held), "heldout_targeted", len(held_t), "ext", len(ext), flush=True)

    def prompt_ids(msgs):
        txt = tok.apply_chat_template(msgs[:2], tokenize=False, add_generation_prompt=True, enable_thinking=False)
        return tok(txt, add_special_tokens=False).input_ids
    def encode(r):
        p = prompt_ids(r["messages"]); a = tok(r["messages"][2]["content"], add_special_tokens=False).input_ids + [EOS]
        return p, a
    p, a = encode(train[0])
    full = tok(tok.apply_chat_template(train[0]["messages"], tokenize=False, enable_thinking=False), add_special_tokens=False).input_ids
    print("prefix-match", full[:len(p)] == p, "full-match", full == p + a, "| tail:", repr(tok.decode(p[-6:])), "| ans end:", repr(tok.decode(a[-4:])), flush=True)

    def score_q(r):
        if r["split"] == "external_test":
            return evq[r["id"].replace("ext-", "")]
        t = r["template"]; kind = t.split("/")[0]
        if kind == "v3":
            return {"id": r["id"], "type": r["type"] + "_" + t.split("/")[1], "gold": r["meta"]["gold"], "recognizedText": r["recognizedText"]}
        typ = ("choose_" + t.split("/")[1]) if (kind.startswith("choose_") or kind == "pron") else (kind + "_" + t.split("/")[1])
        return {"id": r["id"], "type": typ, "gold": r["meta"]["gold"], "recognizedText": r["recognizedText"]}

    rng = random.Random(0); bytpl = collections.defaultdict(list)
    for r in held: bytpl[r["template"]].append(r)
    for v in bytpl.values(): rng.shuffle(v)
    order, k = [], 0
    while len(order) < len(held):
        for t in sorted(bytpl):
            if k < len(bytpl[t]): order.append(bytpl[t][k])
        k += 1
    held_order = order[:A.limit_eval * 2] if A.limit_eval else order
    N200 = A.limit_eval if A.limit_eval else 200

    def ref_cmp(r, sc):
        if r["split"] == "external_test" or not sc.get("steps"): return {}
        ref = json.loads(r["messages"][2]["content"])["steps"]; st = sc["steps"]
        same_n = len(ref) == len(st)
        allexp = same_n and all(kernel.judge(s["expect"], q["expect"]) == "hit" for s, q in zip(st, ref))
        return {"ref_same_nsteps": same_n, "ref_all_expect": allexp}

    RES = P("results.jsonl")
    def done_rows(tag):
        d = {}
        if os.path.exists(RES):
            for l in open(RES):
                try: x = json.loads(l)
                except Exception: continue
                if x.get("tag") == tag: d[x["id"]] = x
        return d
    def sync_cuda():
        if CUDA: torch.cuda.synchronize()
    def run_eval(m, rows, tag, stop_h=None):
        m.eval(); have = done_rows(tag); res = []; t_ph = time.time(); n_new = 0
        for i, r in enumerate(rows):
            if r["id"] in have: res.append(have[r["id"]]); continue
            if stop_h and el() > stop_h: print("eval time stop", tag, i, flush=True); return res, False
            ids = torch.tensor([prompt_ids(r["messages"])], device=DEV)
            sync_cuda(); t0 = time.time()
            with torch.no_grad():
                o = m.generate(input_ids=ids, attention_mask=torch.ones_like(ids), max_new_tokens=A.maxnew, generation_config=GC)
            sync_cuda(); dt = time.time() - t0
            gen = o[0, ids.shape[1]:]; raw = tok.decode(gen, skip_special_tokens=True)
            q = score_q(r)
            x = {"tag": tag, "id": r["id"], "template": r["template"], "recognizedText": r["recognizedText"], "gold": q["gold"],
                 "new_tokens": int(gen.shape[0]), "latency_s": round(dt, 2), "truncated": int(gen.shape[0]) >= A.maxnew, "raw": raw}
            sc = E.score(q, raw); x.update(sc); x.update(ref_cmp(r, sc))
            res.append(x); n_new += 1
            with LOCK:
                with open(RES, "a") as f: f.write(json.dumps(x, ensure_ascii=False) + "\n")
            if i % 20 == 0:
                print(tag, i, f"{dt:.1f}s", x["new_tokens"], "chk", x.get("check_pass"), "fin", x.get("final_correct"), "strict", x.get("strict_pass"), flush=True)
                prog(phase="eval", eval_tag=tag, eval_i=i, eval_n=len(rows), eval_sec_per_item=round((time.time() - t_ph) / max(1, n_new), 2))
        return res, True

    def summ(rows):
        s = E.summarize(rows)
        if s and any("ref_all_expect" in x for x in rows):
            s["ref_all_expect_pct"] = round(100 * sum(1 for x in rows if x.get("ref_all_expect")) / len(rows), 1)
            s["ref_same_nsteps_pct"] = round(100 * sum(1 for x in rows if x.get("ref_same_nsteps")) / len(rows), 1)
        s["truncated_pct"] = round(100 * sum(1 for x in rows if x.get("truncated")) / max(1, len(rows)), 1)
        return s
    METRICS = jload(P("metrics.json"), None) or {"summary": {}}
    METRICS["env"] = {"gpu": torch.cuda.get_device_name(0) if CUDA else "cpu", "train_dtype": str(TR_DT), "eval_dtype": str(EV_DT),
                      "torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__}
    def dump():
        with LOCK: jdump(METRICS, P("metrics.json"))

    # ---------- BEFORE: base model ----------
    b_h, b_t = [], []
    if A.skip_base_eval:
        print("base eval skipped (--skip-base-eval); known v3/v4 base numbers apply", flush=True)
        METRICS["base_eval_skipped"] = True
    elif not PROG.get("base_done"):
        prog(phase="base_eval"); model = load_model(EV_DT)
        b_ext, _ = run_eval(model, ext, "base_ext40"); METRICS["summary"]["base_ext40"] = summ(b_ext); dump()
        b_h, _ = run_eval(model, held_order[:N200], "base_held200"); METRICS["summary"]["base_held200"] = summ(b_h); dump()
        b_t, _ = run_eval(model, held_t, "base_tgt_held147"); METRICS["summary"]["base_tgt_held147"] = summ(b_t); dump()
        print("BASE", json.dumps(METRICS["summary"], ensure_ascii=False), "elapsed_h", round(el(), 2), flush=True)
        del model; prog(base_done=True); sync("base_eval")
        if CUDA: torch.cuda.empty_cache()
    if not A.skip_base_eval:
        b_h = list(done_rows("base_held200").values()); b_t = list(done_rows("base_tgt_held147").values())

    # ---------- LoRA training (resumable) ----------
    if not PROG.get("train_done"):
        prog(phase="train_setup")
        model = load_model(TR_DT); model.config.use_cache = False
        model.enable_input_require_grads()
        cfg = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                         target_modules=["q_k_v_proj", "out_proj", "gate_proj", "up_proj", "down_proj"])
        model = get_peft_model(model, cfg)
        for n, p_ in model.named_parameters():
            if p_.requires_grad: p_.data = p_.data.float()
        model.print_trainable_parameters()
        EXK = '拆题样例（examples，只模仿拆法，答案与本题无关）：\n'
        def strip_ex(u):
            i = u.index(EXK) + len(EXK); tail = '\n请按要求只输出 JSON。'
            j = u.rindex(tail) if tail in u else len(u)
            ex = json.loads(u[i:j])
            for e in ex:
                for st in e["steps"]: st.pop("explain", None)
            return u[:i] + json.dumps(ex, ensure_ascii=False, indent=1) + u[j:]
        arng = random.Random(7); n_aug = 0
        for r in train:
            u = r["messages"][1]["content"]
            if EXK in u and arng.random() < 0.35:
                r["messages"] = [r["messages"][0], {"role": "user", "content": strip_ex(u)}, r["messages"][2]]; n_aug += 1
        print("aug examples-without-explain:", n_aug, flush=True)
        enc = [encode(r) for r in train]
        lens = [len(p) + len(a) for p, a in enc]; print("tok len mean", sum(lens) / len(lens), "max", max(lens), flush=True)
        held_loss_set = [encode(r) for r in held_order[:A.held_loss_n]]
        params = [p_ for p_ in model.parameters() if p_.requires_grad]
        opt = torch.optim.AdamW(params, lr=2e-4, weight_decay=0.0, betas=(0.9, 0.999))
        USE_SCALER = CUDA and TR_DT == torch.float16
        scaler = torch.amp.GradScaler("cuda") if USE_SCALER else None
        ACC, WARM, EPOCHS = A.acc, A.warm, A.epochs
        TOTAL = math.ceil(len(enc) / ACC) * EPOCHS
        sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / WARM) * max(0.03, 0.5 * (1 + math.cos(math.pi * min(1.0, s / TOTAL)))))

        def lossf(p, a):
            ids = torch.tensor([p + a], device=DEV); Pn = len(p)
            with torch.autocast(DEV, dtype=TR_DT, enabled=CUDA):
                out = model(input_ids=ids, logits_to_keep=slice(Pn - 1, ids.shape[1] - 1)); lg = out.logits
            tgt = ids[:, Pn:]
            return F.cross_entropy(lg.float().reshape(-1, lg.shape[-1]), tgt.reshape(-1))
        def held_loss():
            model.eval(); tot = 0
            with torch.no_grad():
                for p, a in held_loss_set: tot += lossf(p, a).item()
            model.train(); return tot / len(held_loss_set)

        # gradient checkpointing: v4 used it (T4 16GB). On A10 24GB try without (same math, faster); probe the longest sample.
        def gc_on():
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        use_gc = A.grad_ckpt == "on"
        if A.grad_ckpt == "auto" and CUDA:
            model.train(); li = max(range(len(enc)), key=lambda i: lens[i])
            try:
                torch.cuda.reset_peak_memory_stats(); lossf(*enc[li]).backward()
                print(f"probe w/o grad-ckpt OK, peak {torch.cuda.max_memory_allocated()/2**30:.1f} GiB (len {lens[li]})", flush=True)
                total_mem = torch.cuda.get_device_properties(0).total_memory
                if torch.cuda.max_memory_allocated() > 0.85 * total_mem: use_gc = True; print("peak too close to limit -> grad-ckpt on", flush=True)
            except torch.cuda.OutOfMemoryError:
                use_gc = True; print("probe OOM -> grad-ckpt on", flush=True)
            opt.zero_grad(set_to_none=True); torch.cuda.empty_cache()
        if use_gc: gc_on()
        print("gradient_checkpointing", use_gc, flush=True)

        LOG = {"steps": [], "epochs": [], "n_aug_noexplain_examples": n_aug, "acc": ACC, "micro_bs": 1, "epochs_planned": EPOCHS,
               "total_opt_steps_sched": TOTAL, "grad_ckpt": use_gc, "train_dtype": str(TR_DT), "train_seconds": 0.0}
        st = {"ep": 0, "j": 0, "step": 0, "micro": 0, "run": []}
        CK = P("checkpoint")
        if os.path.exists(os.path.join(CK, "state.pt")):
            from safetensors.torch import load_file
            S = torch.load(os.path.join(CK, "state.pt"), map_location="cpu", weights_only=False)
            set_peft_model_state_dict(model, load_file(os.path.join(CK, "adapter", "adapter_model.safetensors")))
            opt.load_state_dict(S["opt"]); sched.load_state_dict(S["sched"])
            if scaler and S.get("scaler"): scaler.load_state_dict(S["scaler"])
            if S.get("grads"):
                nm = dict(model.named_parameters())
                for n, g in S["grads"].items(): nm[n].grad = g.to(DEV)
            st = S["st"]; LOG = S["LOG"]; LOG["grad_ckpt"] = use_gc
            torch.set_rng_state(S["rng_cpu"])
            if CUDA and S.get("rng_cuda") is not None: torch.cuda.set_rng_state(S["rng_cuda"])
            random.setstate(S["rng_py"])
            print(f"RESUMED from checkpoint: epoch {st['ep']} pos {st['j']} step {st['step']} micro {st['micro']}", flush=True)
            LOG.setdefault("resumes", []).append({"at_step": st["step"], "time": now()})

        def save_ckpt(tag):
            with LOCK:
                tmp = CK + ".tmp"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
                model.save_pretrained(os.path.join(tmp, "adapter"))
                grads = None
                if st["micro"] % ACC != 0:
                    grads = {n: p_.grad.detach().cpu().clone() for n, p_ in model.named_parameters() if p_.requires_grad and p_.grad is not None}
                torch.save({"opt": opt.state_dict(), "sched": sched.state_dict(), "scaler": scaler.state_dict() if scaler else None,
                            "grads": grads, "st": st, "LOG": LOG, "rng_cpu": torch.get_rng_state(),
                            "rng_cuda": torch.cuda.get_rng_state() if CUDA else None, "rng_py": random.getstate()}, os.path.join(tmp, "state.pt"))
                jdump({"tag": tag, "time": now(), "epoch": st["ep"], "pos": st["j"], "step": st["step"], "micro": st["micro"]}, os.path.join(tmp, "info.json"))
                old = CK + ".old"; shutil.rmtree(old, ignore_errors=True)
                if os.path.exists(CK): os.replace(CK, old)
                os.replace(tmp, CK); shutil.rmtree(old, ignore_errors=True)
                jdump(LOG, P("train_log.json"))
            print(f"[ckpt] saved ({tag}) step {st['step']} epoch {st['ep']} pos {st['j']}", flush=True)

        model.train(); t_seg = time.time(); t_last_ck = time.time(); stopped = False
        sec0 = LOG.get("train_seconds", 0.0); seg0 = st["ep"] * len(enc) + st["j"]
        tr_sec = lambda: sec0 + (time.time() - t_seg)
        prog(phase="train", total_steps=TOTAL, epochs=EPOCHS, n_train=len(enc))
        while st["ep"] < EPOCHS and not stopped:
            ep = st["ep"]; idx = list(range(len(enc))); random.Random(100 + ep).shuffle(idx)
            while st["j"] < len(idx):
                i = idx[st["j"]]
                loss = lossf(*enc[i]) / ACC
                (scaler.scale(loss) if scaler else loss).backward(); st["run"].append(loss.item() * ACC); st["micro"] += 1; st["j"] += 1
                if st["micro"] % ACC == 0:
                    if scaler: scaler.unscale_(opt)
                    torch.nn.utils.clip_grad_norm_(params, 1.0)
                    if scaler: scaler.step(opt); scaler.update()
                    else: opt.step()
                    opt.zero_grad(set_to_none=True); sched.step(); st["step"] += 1
                    if st["step"] % 10 == 0 or A.tiny_model:
                        LOG["train_seconds"] = tr_sec()
                        rec = {"step": st["step"], "epoch": round(ep + st["j"] / len(idx), 3), "loss": round(sum(st["run"]) / len(st["run"]), 4),
                               "lr": sched.get_last_lr()[0], "elapsed_min": round(tr_sec() / 60, 1)}
                        LOG["steps"].append(rec); st["run"] = []; print(rec, flush=True)
                        with LOCK: jdump(LOG, P("train_log.json"))
                        done_samples = ep * len(idx) + st["j"]; tot_samples = EPOCHS * len(idx)
                        rate = (time.time() - t_seg) / max(1, done_samples - seg0)
                        prog(phase="train", epoch=ep + 1, step=st["step"], loss=rec["loss"], lr=rec["lr"], frac=round(done_samples / tot_samples, 4),
                             sec_per_sample=round(rate, 3), train_eta_h=round(rate * (tot_samples - done_samples) / 3600, 2),
                             gpu_peak_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2) if CUDA else None)
                    if (time.time() - t_last_ck) > A.ckpt_min * 60:
                        LOG["train_seconds"] = tr_sec(); save_ckpt("periodic"); t_last_ck = time.time()
                    if A.stop_after_steps and st["step"] >= A.stop_after_steps:
                        LOG["train_seconds"] = tr_sec(); save_ckpt("stop_after_steps"); stopped = True; break
                    if el() > A.train_budget_h:
                        print("TRAIN TIME STOP (session budget) at epoch", ep, "pos", st["j"], flush=True)
                        LOG["train_seconds"] = tr_sec(); save_ckpt("time_budget"); stopped = True; break
            if stopped: break
            hl = held_loss()
            LOG["epochs"].append({"epoch": ep + 1, "held_loss100": round(hl, 4), "elapsed_min": round(tr_sec() / 60, 1), "completed": True})
            print("EPOCH", LOG["epochs"][-1], flush=True)
            with LOCK: model.save_pretrained(P(f"adapter_ep{ep + 1}"))
            st["ep"] += 1; st["j"] = 0; LOG["train_seconds"] = tr_sec()
            save_ckpt(f"epoch{ep + 1}"); t_last_ck = time.time()
            threading.Thread(target=sync, args=(f"epoch{ep + 1}",), daemon=True).start()
            if A.stop_after_epochs and st["ep"] >= A.stop_after_epochs and st["ep"] < EPOCHS: stopped = True
        if stopped:
            open(P("PAUSED"), "w").write(f"{now()} paused at epoch {st['ep']} pos {st['j']} step {st['step']}; rerun run.sh to resume\n")
            prog(phase="paused_train"); sync("paused"); print("PAUSED (rerun run.sh to resume)", flush=True); return 3
        LOG["train_minutes"] = round(tr_sec() / 60, 1); LOG["opt_steps"] = st["step"]
        with LOCK:
            jdump(LOG, P("train_log.json"))
            model.save_pretrained(P("adapter")); tok.save_pretrained(P("adapter"))
            shutil.make_archive(P("adapter"), "zip", P("adapter"))
        METRICS["train"] = {k: LOG.get(k) for k in ["train_minutes", "opt_steps", "epochs_planned", "acc", "micro_bs", "grad_ckpt", "train_dtype"]}
        METRICS["train"]["sec_per_sample"] = round(LOG["train_seconds"] / max(1, EPOCHS * len(enc)), 3)
        METRICS["train"]["epochs"] = LOG["epochs"]; METRICS["train"]["last_loss"] = LOG["steps"][-1] if LOG["steps"] else None; dump()
        prog(train_done=True, phase="train_done"); sync("train_done")
        del model, opt
        if CUDA: torch.cuda.empty_cache()

    # ---------- AFTER: merged LoRA eval (resumable) ----------
    prog(phase="lora_eval")
    base = load_model(EV_DT)
    model = PeftModel.from_pretrained(base, P("adapter")).merge_and_unload().to(EV_DT)
    model.config.use_cache = True; model.eval(); model.generation_config = GC
    l_ext, ok1 = run_eval(model, ext, "lora_ext40", stop_h=A.eval_stop_h); METRICS["summary"]["lora_ext40"] = summ(l_ext); dump()
    l_h, ok2 = run_eval(model, held_order[:N200], "lora_held200", stop_h=A.eval_stop_h); METRICS["summary"]["lora_held200"] = summ(l_h); dump()
    l_t, ok3 = run_eval(model, held_t, "lora_tgt_held147", stop_h=A.eval_stop_h); METRICS["summary"]["lora_tgt_held147"] = summ(l_t); dump()
    l_rest, ok4 = run_eval(model, held_order[N200:], "lora_held_rest", stop_h=A.eval_stop_h)
    METRICS["summary"]["lora_held_all_done"] = summ(l_h + l_rest)
    METRICS["summary"]["lora_held_combined_v2+tgt"] = summ(l_h + l_rest + l_t); dump()
    bt = {}
    for tg, rows in [("base", b_h + b_t), ("lora", l_h + l_rest + l_t)]:
        for t in sorted({x["template"] for x in rows}):
            sub = [x for x in rows if x["template"] == t]; s = summ(sub)
            bt[f"{tg}|{t}"] = {k: s.get(k) for k in ["n", "check_pass_pct", "final_correct_pct", "strict_pass_pct", "forbid_violation_pct"]}
    METRICS["by_template"] = bt; METRICS["elapsed_h_this_run"] = round(el(), 2); dump()
    print("FINAL", json.dumps(METRICS["summary"], ensure_ascii=False), flush=True)
    if not (ok1 and ok2 and ok3 and ok4):
        open(P("PAUSED"), "w").write(f"{now()} eval paused by time budget; rerun run.sh to resume\n")
        prog(phase="paused_eval"); sync("paused_eval"); return 3
    open(P("DONE"), "w").write(f"{now()} DONE\n" + json.dumps(METRICS["summary"], ensure_ascii=False) + "\n")
    prog(phase="done"); sync("done"); print("DONE", flush=True)
    return 0

if __name__ == "__main__":
    try:
        rc = main()
    except BaseException as e:
        tb = traceback.format_exc()
        if TOKEN: tb = tb.replace(TOKEN, "***")
        print(tb, flush=True)
        open(P("ERROR"), "w").write(f"{now()}\n{tb}")
        try: prog(phase="error", error=f"{type(e).__name__}: {str(e)[:300]}")
        except Exception: pass
        try: sync("error")
        except Exception: pass
        sys.exit(1)
    sys.exit(rc)
