# Measuring wrong YESes in APPARE's LLM profiler

[APPARE](https://github.com/ASTERISC-Release/Appare) expands each syscall's reference function set by asking an LLM whether a profiled caller should be expected to reach an unprofiled callee (`guest/kern-cfg/run-slice-client.py`). The paper's validation (§7.2) samples only caller→callee pairs taken from the profiled traces, so every test pair should get YES. That check can count wrong NOs, but not wrong YESes: unneeded functions the LLM adds to the allow-list. The paper names this risk as a threat to validity (§7.4) but does not measure it.

This repo measures it. APPARE is included unmodified as a submodule (pinned to `7f5b473`); the scripts here only read its files.

## The test

200 questions, each asked with **APPARE's own prompt**, copied verbatim from `run-slice-client.py` lines 138–162. That includes the backward-slice call tree, the source of every function in the slice, and the caller and callee source. The seed is the paper's `nginx-ltp-redis` profile.

| Group | Count | Definition | Correct answer |
|---|---|---|---|
| `stranger` | 100 | Edge A→B in `pruned_callgraph_filtered.graphml` (the graph APPARE walks), A in the seed, B one hop out and not in the seed. B never appears in any recording of that syscall by any program, is ftrace-traceable (`fentry-set-raw.txt`), and passes `kfunc_filter.py`. | NO |
| `hidden_real` | 100 | Edge A→B where both A and B ran in a held-out program (cp, curl, ls, statfs01, tar, unqlite, wget) and B is not in the seed. | YES |

Each group allows at most 2 questions per caller, counted across all syscalls. The exam is frozen in `llm-exam/exam_all.json` (random seed 42); use that file for every comparison.

## Results so far

Qwen3-8B, local, APPARE's prompt unchanged:

| | YES rate |
|---|---|
| Strangers (wrong YES) | **73%** (73/100, ±9) |
| Hidden real (correct YES) | **71%** (71/100, ±9) |

The model says YES at about the same rate whether or not the function belongs.

Cross-check against Qwen3-32B: 67 of the 200 questions appear verbatim in APPARE's saved logs (`syscall_procs/*/llm_logs_nothink.txt`). On those, 32B said YES to 11/23 strangers (48%) and 30/44 hidden real (68%).

Other observations:
- The prompt's `\nothink` ending did not disable Qwen3's thinking: all 200 answers included a thinking block.
- About a third of the naive negatives were not ftrace-traceable (e.g. `_copy_to_user`). Their absence from recordings proves nothing, so they are excluded.

## Caveats

- The wrong-YES rate is an **upper estimate**. Some strangers are rare but benign paths (e.g. error cleanup) that the recordings never hit.
- The main numbers come from 8B. The 32B comparison covers only 67 questions.
- Depth-0 questions only, against the original seed graph. The real tool's history grows as it accepts functions.
- The released prompt has no function summaries or global struct context (described in paper §5.1.2), so neither does this test.

## Reproduce

Needs [Ollama](https://ollama.com/) and about 10 GB of free memory for Qwen3-8B. The submodule is a large repository (it includes two Linux source trees).

```bash
git clone --recurse-submodules <this repo>
cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen3:8b              # and keep `ollama serve` running

python llm-exam/build_exam.py      # optional: rebuilds exam_all.json (same seed, same exam)
python llm-exam/run_exam.py        # ~65 s/question on a 16 GB M-series Mac; resumable
python llm-exam/score_exam.py llm-exam/answers_qwen3-8b_baseline.jsonl
```

`run_exam.py --tag <name>` writes a separate answers file per variant, so later runs (e.g. new prompt wording, thinking off) can be compared on the same exam.
