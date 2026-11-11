"""
Ask the LLM every question in the exam and save each answer.

- Uses the authors' settings: no temperature set (model defaults), num_ctx 32768.
  We only add a fixed "seed" so that re-running gives the same answers.
- Reads the answer exactly like the authors' tool: YES only if the reply
  contains "FINAL ANSWER -> YES", otherwise NO.
- Saves after EVERY question. If you stop it (Ctrl-C) or the Mac sleeps, just
  run the same command again and it continues where it left off.

Usage (from ~/Developer/appare, venv active, `ollama serve` running):
    python llm-exam/run_exam.py --limit 4      # quick trial: first 4 questions
    python llm-exam/run_exam.py                # the whole exam
"""
import argparse
import json
import os
import re
import time

from ollama import Client

HERE = os.path.dirname(__file__)
THINK_BLOCK = re.compile(r"<think>(.*?)</think>", flags=re.DOTALL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exam", default=os.path.join(HERE, "exam_all.json"))
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--tag", default="baseline", help="name for this run, e.g. baseline or fix-source")
    ap.add_argument("--num-ctx", type=int, default=32768, help="same as the authors")
    ap.add_argument("--seed", type=int, default=42, help="makes answers repeatable")
    ap.add_argument("--limit", type=int, default=0, help="only ask the first N questions (0 = all)")
    ap.add_argument("--host", default="http://localhost:11434")
    args = ap.parse_args()

    questions = json.load(open(args.exam))["questions"]
    if args.limit:
        questions = questions[: args.limit]

    out = os.path.join(HERE, f"answers_{args.model.replace(':', '-')}_{args.tag}.jsonl")
    done = set()
    if os.path.exists(out):
        with open(out) as f:
            done = {json.loads(line)["id"] for line in f if line.strip()}
    todo = [q for q in questions if q["id"] not in done]
    print(f"{len(done)} already answered, {len(todo)} to go. Saving to {out}")

    client = Client(host=args.host)
    with open(out, "a") as f:
        for n, q in enumerate(todo, 1):
            start = time.time()
            r = client.chat(
                model=args.model,
                messages=[{"role": "user", "content": q["prompt"]}],
                options={"num_ctx": args.num_ctx, "seed": args.seed},
            )
            seconds = time.time() - start

            content = r.message.content or ""
            # How much "thinking" did the model do? (Tells us whether the
            # authors' "\nothink" ending really switched thinking off.)
            thinking = (getattr(r.message, "thinking", None) or "") + "".join(THINK_BLOCK.findall(content))
            reply = THINK_BLOCK.sub("", content).strip()

            # Same rule as the authors' tool
            answer = "YES" if "FINAL ANSWER -> YES" in reply.upper() else "NO"
            unclear = "FINAL ANSWER -> YES" not in reply.upper() and "FINAL ANSWER -> NO" not in reply.upper()

            record = {
                "id": q["id"], "group": q["group"], "correct": q["correct"],
                "syscall": q["syscall"], "caller": q["caller"], "callee": q["callee"],
                "answer": answer, "right": answer == q["correct"], "unclear": unclear,
                "seconds": round(seconds, 1),
                "prompt_tokens": getattr(r, "prompt_eval_count", None),
                "thinking_chars": len(thinking),
                "reply": reply,
            }
            f.write(json.dumps(record) + "\n")
            f.flush()

            mark = "ok   " if record["right"] else "WRONG"
            print(f"[{n}/{len(todo)}] {mark} {q['group']:<11} said {answer:<3} "
                  f"{q['caller']} -> {q['callee']}  ({seconds:.0f}s, thinking {len(thinking)} chars)")

    print(f"\nDone. Score it with: python llm-exam/score_exam.py {out}")


if __name__ == "__main__":
    main()
