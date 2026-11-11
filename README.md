# Measuring wrong YESes in APPARE's LLM profiler

This experiment evaluates the LLM inference step of [APPARE](https://github.com/ASTERISC-Release/Appare) ([Zhang et al., IEEE S&P 2026](https://www.chuqiz.com/papers/appare_paper.pdf)). APPARE is included unmodified as a submodule, pinned to `7f5b473`.

## Question

The paper's validation (§7.2) only tests caller→callee pairs that actually occurred in the profiled traces, so it can't see the LLM adding functions that don't belong. **How often does the LLM wrongly say YES?**

## Setup

- **Model:** Qwen3-8B via Ollama (fixed seed, `num_ctx` 32768).
- **Prompt:** APPARE's own prompt and retrieved context, copied verbatim from `guest/kern-cfg/run-slice-client.py`: backward-slice call tree, slice source code, caller and callee source. Seed = the paper's `nginx-ltp-redis` profiles.
- **"NO" questions (never ran):** call-graph neighbors. Each is an edge A→B in APPARE's `pruned_callgraph_filtered.graphml`, with A in the seed and B one hop out. B never appears in any recording of that syscall by any program, and it is ftrace-traceable (`fentry-set-raw.txt`), so its absence is meaningful.
- **"YES" questions (hidden):** edges A→B where both functions ran in a program left out of the seed (cp, curl, ls, statfs01, tar, unqlite, wget), with B not in the seed.
- **Sampling:** at most 2 questions per caller, drawn from all syscalls (the NO group spans 59 syscalls, the YES group 22). The exam is frozen in `llm-exam/exam_all.json`.

## Results

| Question type | Questions | Model said YES |
|---|---|---|
| Never ran (YES is wrong) | 100 | **73%** (±9) |
| Hidden but ran (YES is right) | 100 | **71%** (±9) |

The model says YES at the same rate whether or not the function belongs. Cross-check: on the 67 questions that also appear in APPARE's saved Qwen3-32B logs, 32B said YES to 48% (11/23) of never-ran functions and 68% (30/44) of hidden ones.

## Limitations

- **Upper bound:** some never-ran functions may be rare but legitimate paths (e.g. error cleanup) that the recordings missed.
- **Smaller model:** the main results use Qwen3-8B. APPARE uses Qwen3-32B, and the 32B cross-check covers only 67 questions.
- **Sample size:** 100 questions per group gives about ±9 points of uncertainty, and only one step out from the seed is tested.

## How to reproduce

```bash
git clone --recurse-submodules <this repo> && cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen3:8b                 # keep `ollama serve` running

python llm-exam/run_exam.py --tag rerun     # ~65 s per question on a 16 GB Mac; resumable
python llm-exam/score_exam.py llm-exam/answers_qwen3-8b_rerun.jsonl
```

To score the original run instead: `python llm-exam/score_exam.py llm-exam/answers_qwen3-8b_baseline.jsonl`. `llm-exam/build_exam.py` regenerates the same exam from APPARE's files.
