"""用现有 v4 Q4 模型（不重训）试新输入格式。需要 llama-server 在 :8091 运行：
  llama-server -m ../gguf/merged_v4-Q4_K_M.gguf -t 8 -c 4096 --port 8091"""
import json, time, urllib.request, sys
import planner as P
from demo import ITEMS
TSYS = open('/workspace/xiangke_small_model/merged_v4/system_prompt.txt').read().strip()
GBNF = open('/workspace/xiangke_small_model/gguf/diag_user4/steps.gbnf').read()
URL = 'http://127.0.0.1:8091/completion'

def gen(sys_, user, grammar):
    # 与 merged_v4 chat_template(enable_thinking=False) 渲染结果逐 token 一致（已用 gguf/sanity/prompt_ids.json 验证）
    BOS, EOS = '<｜start▁of▁sentence｜>', '<｜end▁of▁sentence｜>'
    txt = f'{BOS}<|System|>\nyou are a helpful assistant.\n\n{sys_}{EOS}{BOS}<|User|>{user}{EOS}{BOS}<|Bot|></think>'
    ids = txt
    b = {'prompt': ids, 'n_predict': 600, 'temperature': 0, 'top_k': 1, 'seed': 0, 'cache_prompt': False, 'grammar': grammar}
    t = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request(URL, json.dumps(b).encode(), {'Content-Type': 'application/json'})))
    return r['content'], round(time.time() - t, 1), r['timings']['predicted_n']

def pipeline(item, gold, sys_, grammar_mode):
    plan = P.plan_steps(item, gold)
    g = GBNF if grammar_mode == 'steps.gbnf' else P.plan_grammar(plan)
    log = []
    u = P.build_user_message(item, plan)
    for attempt in (1, 2):
        raw, dt, n = gen(sys_, u, g)
        ok, errs, _ = P.check_steps(raw, plan)
        log.append({'attempt': attempt, 'raw': raw, 'ok': ok, 'errors': errs, 'latency_s': dt, 'tokens': n})
        if ok: return {'final': json.loads(raw), 'source': f'model_try{attempt}', 'log': log}
        u = P.retry_message(item, plan, errs)
    try: steps = json.loads(raw)['steps']
    except Exception: steps = None
    pt = P.patch_with_template(steps, plan, errs)
    if pt: return {'final': pt[0], 'source': f'model+template_patch(steps {pt[1]})', 'log': log}
    return {'final': P.template_fallback(plan), 'source': 'template_fallback', 'log': log}

CFGS = [('A_trainSys+steps.gbnf', TSYS, 'steps.gbnf'), ('B_shortSys+steps.gbnf', P.SHORT_SYSTEM, 'steps.gbnf'),
        ('C_shortSys+planLockedGBNF', P.SHORT_SYSTEM, 'locked')]

def sample_eval(n_per_type=5, cfg_idx=2, seed=0):
    """heldout 每类抽 n 条，统计 一次过/重试过/逐步补丁/整题模板 的比例。"""
    import random, collections
    rows = [json.loads(l) for l in open('/workspace/xiangke_small_model/lora/ms_job/ds_v4/heldout.jsonl')]
    random.Random(seed).shuffle(rows)
    pick = collections.defaultdict(list)
    for r in rows:
        p = P.plan_steps(r['recognizedText'], r['meta']['gold'])
        if not p['fallback'] and len(pick[p['type']]) < n_per_type: pick[p['type']].append(r)
    name, s, gm = CFGS[cfg_idx]; out = []; cnt = collections.Counter()
    for ty, rs in pick.items():
        for r in rs:
            x = pipeline(r['recognizedText'], r['meta']['gold'], s, gm)
            src = x['source'].split('(')[0]; cnt[(ty, src)] += 1
            out.append({'type': ty, 'item': r['recognizedText'], 'gold': r['meta']['gold'], **x})
            print(ty, '|', r['recognizedText'], '->', x['source'], [l['errors'] for l in x['log']], flush=True)
    json.dump(out, open(f'out/sample_eval_{name}.json', 'w'), ensure_ascii=False, indent=1)
    for k, v in sorted(cnt.items()): print(k, v)

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "sample":
    sample_eval(int(sys.argv[2]) if len(sys.argv) > 2 else 5, int(sys.argv[3]) if len(sys.argv) > 3 else 2)
elif __name__ == "__main__":  # 4 道演示题 × 3 种配置
    res = []
    for name, s, gm in CFGS:
        for item, gold in ITEMS:
            r = pipeline(item, gold, s, gm); r.update(cfg=name, item=item, gold=gold); res.append(r)
            print('=' * 70, '\n', name, '|', item, '->', r['source'], flush=True)
            for l in r['log']:
                print(f"  try{l['attempt']} ok={l['ok']} {l['latency_s']}s {l['tokens']}tok errs={l['errors']}")
                print('   raw:', l['raw'])
    json.dump(res, open('out/model_runs.json', 'w'), ensure_ascii=False, indent=1)
