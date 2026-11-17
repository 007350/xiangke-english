# planner_v5：程序定步骤，模型只润色

**思路**：每道题的步骤数、每步的 expect 和规则都由程序（`planner.py`）决定。模型（v4 Q4）只负责把 ask/explain 改写成温和、简单的中文。只在 CPU 上推理，不训练，不上传。

## 文件
- `planner.py`：核心代码。包括 `detect_type`、`find_subject`、`diff_word`、`spell_slots`、`cue_word`、`RULES`（27 条）/`get_rule`、`plan_steps`、`check_steps`、`build_user_message`、`retry_message`、`template_fallback`、`plan_grammar`（锁定步骤的 GBNF）和 `patch_with_template`。
- `demo.py`：4 道演示题的步骤计划，输出到 `out/demo_plans.{json,txt}`。
- `coverage.py`：在 ds_v4 的 train/heldout 上统计覆盖率并抽查，输出到 `out/coverage.json`。
- `run_model.py`：调用模型。不带参数时，跑 4 道演示题 × 3 种配置，输出到 `out/model_runs.{json,txt}`。`sample N CFG` 会在 heldout 上每类抽 N 题，输出到 `out/sample_eval_*.json`。

## 题型和固定的步骤形状
- `be_verb`：1) 找主语，判断一个还是多个；2) 选 am/is/are
- `plural_noun`：1) 找提示词；2) 选或写名词形式
- `verb_3s`：1) 找出错的词；2) 写出改正后的词
- `spelling`：每个空一步，最后一步写出整个单词

程序无法确认时（规则推出的答案 ≠ gold），退回成单步 `whole`。

## 运行
```
cd out && /workspace/tools/llama.cpp/build/bin/llama-server -m ../../gguf/merged_v4-Q4_K_M.gguf -t 8 -c 4096 --port 8091 --no-ui &
cd .. && python3 demo.py && python3 coverage.py && python3 run_model.py && python3 run_model.py sample 5 2
```
解码设置：greedy（temp 0，top_k 1）。配置 A 用训练时的 system prompt + `steps.gbnf`，B 用短 system prompt + `steps.gbnf`，C 用短 system prompt + 锁定步骤的 GBNF。

## 检查和兜底流程
`check_steps` 检查以下几点：
- JSON 结构正确
- 步骤数和计划一致
- 每步 expect 和计划一致
- 前面的步骤没有提前说出后面的答案
- ask 里没有直接说出本步答案
- 没有用术语（第三人称、单数等）
- 拼写题的 ask 里不出现完整单词
- 非 be 题里不出现 am/is/are

兜底顺序：第 1 次生成 → 检查 → 带着错误信息重试 1 次 → 只把不合格的步骤换成模板（patch） → 整题改用模板。

## 结果（概要）
- 覆盖率：be 300/300，plural 861/864，3s 167/167，spelling 268/268。所有 expect 都和 gold 一致，模板全部能通过检查。
- heldout 抽样（配置 C，每类 5 题）：be 5/5、spelling 5/5 第 1 次就通过；plural 4 题第 1 次通过、1 题重试后通过；3s 第 1 次通过 2、重试后通过 1、需要 patch 2。主要问题是用了术语，或借用了 am/is/are 的口诀。
