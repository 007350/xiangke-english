import json, planner as P
ITEMS = [("They ___ my friends. (am / is / are)", "are"), ("This is an ___. (apple / apples)", "apple"),
         ("改错（只改一个词）：She like cats.", "likes"), ("看中文写单词：书 b__k", "book")]
if __name__ == '__main__':
    out = []
    for t, g in ITEMS:
        p = P.plan_steps(t, g); fb = P.template_fallback(p)
        ok, errs, _ = P.check_steps(fb, p)
        print('=' * 60); print(t, '| gold', g, '| type', p['type'], '| fallback', p['fallback'])
        for i, s in enumerate(p['steps'], 1): print(f"  {i}. [{s['intent']}] expect={s['expect']!r} rule={s['rule']}")
        print('  template:', json.dumps(fb, ensure_ascii=False)); print('  template check:', ok, errs)
        out.append({'item': t, 'gold': g, 'plan': p, 'template': fb, 'template_check': [ok, errs], 'user_message': P.build_user_message(t, p)})
    json.dump(out, open('out/demo_plans.json', 'w'), ensure_ascii=False, indent=1)
    print(out[0]['user_message'])
