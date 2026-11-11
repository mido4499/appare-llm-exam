"""
Score an answers file. Works on a finished or a half-finished run.

Usage:
    python llm-exam/score_exam.py llm-exam/answers_qwen3-8b_baseline.jsonl
"""
import json
import math
import sys


def rate(hits, total):
    """Percentage, plus a rough 'give or take' (95% margin) for this sample size."""
    if total == 0:
        return "n/a"
    p = hits / total
    margin = 1.96 * math.sqrt(p * (1 - p) / total)
    return f"{100 * p:5.1f}%  (give or take {100 * margin:.0f} points; {hits}/{total})"


rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
strangers = [r for r in rows if r["group"] == "stranger"]
hidden = [r for r in rows if r["group"] == "hidden_real"]

print(f"Answers so far: {len(rows)}\n")
print("Wrong-YES rate (strangers it let in)  lower is better: "
      + rate(sum(r["answer"] == "YES" for r in strangers), len(strangers)))
print("Catch rate (hidden real it let in)    higher is better: "
      + rate(sum(r["answer"] == "YES" for r in hidden), len(hidden)))

# Strangers from the heavily recorded syscalls only (nginx/redis-style ones,
# where "never recorded" is stronger evidence). Same syscalls as the real group.
busy = {r["syscall"] for r in hidden}
s_busy = [r for r in strangers if r["syscall"] in busy]
print("Wrong-YES rate, well-recorded syscalls only:           "
      + rate(sum(r["answer"] == "YES" for r in s_busy), len(s_busy)))

print()
print(f"Unclear replies (no FINAL ANSWER line): {sum(r['unclear'] for r in rows)}")
print(f"Average time per question: {sum(r['seconds'] for r in rows) / max(len(rows), 1):.1f}s")
print(f"Questions where the model 'thought' first: {sum(r['thinking_chars'] > 0 for r in rows)}")
