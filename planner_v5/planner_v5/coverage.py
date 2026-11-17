import json, collections, planner as P
D = '/workspace/xiangke_small_model/lora/ms_job/ds_v4/'
TARGET = {'be_verb': ['choose_be/be'], 'plural_noun': ['choose_plural/plural', 'fill_plural/plural', 'v3/ctx_plural_choose/ctx_plural', 'v3/ctx_plural_fill/ctx_plural'],
          'verb_3s': ['fix/fix_3s', 'v3/fix_3s/fix_3s_gloss'], 'spelling': ['spell/spell_missing', 'v3/spell/spell_missing']}
T2 = {t: k for k, v in TARGET.items() for t in v}
rep = {}; bad = []; samples = collections.defaultdict(list)
for split in ['train', 'heldout']:
    rows = [json.loads(l) for l in open(D + split + '.jsonl')]
    per = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        tx, g, tpl = r['recognizedText'], r['meta']['gold'], r['template']
        p = P.plan_steps(tx, g)
        want = T2.get(tpl, 'other')
        c = per[want]; c['n'] += 1
        if p['fallback']:
            c['fallback'] += 1; c['why:' + p['why'].split(':')[0] + ':' + p['why'].split(':')[-1][:24]] += 1
            if want != 'other' and len(samples[want]) < 6: samples[want].append((tx, g, p['why']))
            continue
        c['planned'] += 1
        if p['type'] != want: c['WRONG_TYPE:' + p['type']] += 1; bad.append((split, tpl, tx, g, p['type']))
        ok, errs, _ = P.check_steps(P.template_fallback(p), p)
        if not ok: c['template_check_fail'] += 1; bad.append((split, tpl, tx, g, errs))
        # 对照数据集原有 gold 步骤：中间 expect 是否也出现在原步骤里
        gs = json.loads(r['messages'][2]['content'])['steps']
        ge = [s['expect'].lower() for s in gs]
        if gs[-1]['expect'] != p['steps'][-1]['expect']: c['final_mismatch'] += 1
        if p['type'] == 'verb_3s':
            c['wrong_word_matches_gold_step1'] += int(p['steps'][0]['expect'].lower() == ge[0])
        if p['type'] == 'be_verb':
            c['subject_in_gold_steps'] += int(any(p['meta']['subject'].lower() == x for x in ge))
    rep[split] = {k: dict(v) for k, v in per.items()}
json.dump({'coverage': rep, 'problems': bad[:200], 'fallback_samples': samples}, open('out/coverage.json', 'w'), ensure_ascii=False, indent=1)
for split, d in rep.items():
    print('==', split)
    for k, v in sorted(d.items()): print(' ', k, v)
print('problems', len(bad)); [print(' ', b) for b in bad[:20]]
for k, v in samples.items(): print(k); [print('  ', x) for x in v]
