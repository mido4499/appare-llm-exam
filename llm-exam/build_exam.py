"""
Build the "stranger vs. real-but-hidden" exam across all syscalls.

The LLM is given the authors' exact seed (nginx + LTP + redis). Two groups:
  - stranger:    the callee never ran during that syscall in any recording -> NO
  - hidden_real: a hidden program (cp, curl, tar, ...) ran the call      -> YES

It only builds the list of questions and saves it to a file. It does NOT talk
to the LLM; run_exam.py does that. Splitting them lets you look at the
questions before spending an hour asking them.

Usage (from ~/Developer/appare, with the venv active):
    python llm-exam/build_exam.py
"""
import argparse
import glob
import json
import os
import random
import re
import sys

import networkx as nx

# Use the authors' own noise filter so we skip the same functions they skip.
KERN_CFG = os.path.join(os.path.dirname(__file__), "..", "Appare", "guest", "kern-cfg")
sys.path.insert(0, KERN_CFG)
from kfunc_filter import should_filter_function  # noqa: E402


# ---------------------------------------------------------------------------
# Copied unchanged from Appare/guest/kern-cfg/tools.py. (That file also imports
# matplotlib, which we don't need, so we copy just these two helpers.)
# ---------------------------------------------------------------------------
def backward_slice(graph, node):
    backward_nodes = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if current in backward_nodes:
            continue
        backward_nodes.add(current)
        if graph.in_degree(current) == 0:
            continue
        for predecessor in graph.predecessors(current):
            if predecessor not in backward_nodes:
                stack.append(predecessor)
    return graph.subgraph(backward_nodes).copy()


def graph_to_markdown_tree(graph, note_profiled=False):
    def dfs(node, indent=0, visited=set()):
        lines = []
        prefix = "  " * indent + "- " + node
        if note_profiled and graph.nodes[node].get("profiled", False):
            prefix += " <profiled>"
        lines.append(prefix)
        visited.add(node)
        for _, neighbor in graph.out_edges(node):
            if neighbor not in visited:
                lines.extend(dfs(neighbor, indent + 1, visited))
        return lines
    roots = [n for n in graph.nodes if graph.in_degree(n) == 0]
    all_lines = []
    for root in roots:
        all_lines.extend(dfs(root, indent=0, visited=set()))
    return "\n".join(all_lines)


# ---------------------------------------------------------------------------
# The authors' prompt, copied word for word from run-slice-client.py
# (lines 138-162), including the "\nothink" ending. This is the "before" test,
# so we don't change anything about the question.
# ---------------------------------------------------------------------------
def authors_prompt(src, dst, dyn_graph, function_sources, all_blocks):
    back_slice_grh = backward_slice(dyn_graph, src)
    markdown_output = graph_to_markdown_tree(back_slice_grh)

    historical_srccode = "Here are some Linux kernel function's source code:\n"
    for node in back_slice_grh.nodes:
        code = function_sources.get(node, "")
        if code:
            historical_srccode += f"-- {node}:\n{code}\n"

    prompt_text = (
        f"Please first read the above functions' source code.\n"
        f"You are a Linux security expert analyzing kernel call-graph edges.\n"
        f"Historical dynamic function call-graph (from prior executions):\n{markdown_output}\n\n"
        f"Caller: {src}\nSource code:\n{function_sources.get(src)}\n\n"
        f"Callee candidate: {dst}\nSource code:\n{all_blocks.get(dst)}\n\n"
    )
    question_text = (
        f"\nFrom a security-enforcement standpoint, and given the historical execution contexts, "
        f"please predict is it semantically and functionally reasonable to expect that "
        f"execution of {src} will reach {dst}? Provide a concise justification, "
        f"then a literal answer: '{{Your justification}}\nFINAL ANSWER -> YES/NO'\\nothink"
    )
    return historical_srccode + prompt_text + question_text


def read_names(path):
    """One function name per line -> set of names."""
    with open(path) as f:
        return {line.strip() for line in f if line.strip()}


def base_name(name):
    """'foo.isra.0' -> 'foo'. Compiler-renamed copies count as the same function."""
    return name.split(".", 1)[0]


# Programs that were recorded but are NOT part of the authors' seed
# (the seed is nginx + LTP + redis). We hide these from the LLM.
OTHER_PROGRAMS = ["cp-0.txt", "curl-0.txt", "ls-0.txt", "statfs01-0.txt",
                  "tar-0.txt", "unqlite-0.txt", "wget-0.txt"]


def cap_per_caller(pairs, cap, rng):
    """Keep at most `cap` random pairs per caller, so no single caller
    (like dev_attr_show, with hundreds of callees) takes over the exam."""
    by_caller = {}
    for p in pairs:
        by_caller.setdefault(p["caller"], []).append(p)
    kept = []
    for caller in sorted(by_caller):
        group = by_caller[caller]
        kept += rng.sample(group, min(cap, len(group)))
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-group", type=int, default=100, help="questions per group")
    ap.add_argument("--max-per-caller", type=int, default=2, help="limit per caller, across all syscalls")
    ap.add_argument("--random-seed", type=int, default=42, help="same seed -> same questions")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "exam_all.json"))
    args = ap.parse_args()

    # --- 1. Load the kernel call map (same file the authors' tool walks) ---
    print("Loading call graph (takes ~30s)...")
    k_cg = nx.read_graphml(os.path.join(KERN_CFG, "pruned_callgraph_filtered.graphml"))

    # --- 2. Load every function's source code (same parsing as the authors) ---
    with open(os.path.join(KERN_CFG, "the_functions_all.txt")) as f:
        content = f.read()
    pattern = r"Source Code for\s+([\w_]+)\s*:\s*\n(.*?)(?=XXXTHISENDSHEREXXX)"
    all_blocks = dict(re.findall(pattern, content, flags=re.DOTALL))

    # Functions the recorder can see. Others never show up in
    # any recording, so "never recorded" says nothing about them
    with open(os.path.join(KERN_CFG, "..", "vmhome", "kftracer", "fentry-set-raw.txt")) as f:
        recordable = set(f.read().split())

    # --- 3. Go through every syscall that has the authors' seed list ---
    profiles_root = os.path.join(KERN_CFG, "syscall_profiles")
    strangers, hidden_real = [], []
    seed_graphs = {}  # syscall -> the authors' known-functions graph for it

    for syscall in sorted(os.listdir(profiles_root)):
        prof_dir = os.path.join(profiles_root, syscall)
        seed_file = os.path.join(prof_dir, "nginx-ltp-redis")
        if not os.path.isfile(seed_file):
            continue

        # What the LLM gets to see: the authors' exact seed (nginx + LTP + redis)
        seed = {f for f in read_names(seed_file) if f in k_cg and not should_filter_function(f)}
        seed_graphs[syscall] = k_cg.subgraph(seed).copy()

        # Everything that ran during this syscall in ANY recording of ANY
        # program. Not in here = stranger. (unprofiled-fs.txt is a report, skip it.)
        ever_ran = set()
        for path in glob.glob(os.path.join(prof_dir, "*")):
            if os.path.basename(path) != "unprofiled-fs.txt":
                ever_ran |= {base_name(n) for n in read_names(path)}

        # What the hidden programs (cp, curl, tar, ...) ran
        others = set()
        for name in OTHER_PROGRAMS:
            path = os.path.join(prof_dir, name)
            if os.path.isfile(path):
                others |= read_names(path)

        # Walk one step out from every seed function and sort into groups
        for a in sorted(seed):
            for _, b in sorted(k_cg.out_edges(a)):
                if b in seed or should_filter_function(b):
                    continue  # already known, or background noise
                pair = {"syscall": syscall, "caller": a, "callee": b}
                if base_name(b) not in ever_ran and base_name(b) in recordable:
                    # Nobody ever ran b during this syscall -> correct answer NO
                    strangers.append(pair)
                elif a in others and b in others:
                    # A hidden program ran both, the map says a can call b,
                    # and the seed never showed b -> correct answer YES
                    hidden_real.append(pair)

    print(f"Syscalls used:                 {len(seed_graphs)}")
    print(f"Possible stranger questions:   {len(strangers):,}")
    print(f"Possible real-but-hidden ones: {len(hidden_real):,}")

    # --- 4. Limit each caller, then pick a random sample of each group ---
    #     Same random seed -> exactly the same exam every time.
    rng = random.Random(args.random_seed)
    picked = []
    for group, pairs in [("stranger", strangers), ("hidden_real", hidden_real)]:
        capped = cap_per_caller(pairs, args.max_per_caller, rng)
        sample = rng.sample(capped, min(args.per_group, len(capped)))
        print(f"  {group}: {len(capped):,} left after the per-caller limit, picked {len(sample)}")
        picked += [(group, p) for p in sample]

    # --- 5. Write the questions, using the authors' exact prompt ---
    questions = []
    for i, (group, p) in enumerate(picked):
        g = seed_graphs[p["syscall"]]
        sources = {n: all_blocks.get(n) for n in g.nodes}
        prompt = authors_prompt(p["caller"], p["callee"], g, sources, all_blocks)
        questions.append({
            "id": i,
            "group": group,
            "correct": "NO" if group == "stranger" else "YES",
            "syscall": p["syscall"],
            "caller": p["caller"],
            "callee": p["callee"],
            "callee_source_missing": all_blocks.get(p["callee"]) is None,
            "prompt_chars": len(prompt),
            "prompt": prompt,
        })

    with open(args.out, "w") as f:
        json.dump({"random_seed": args.random_seed, "max_per_caller": args.max_per_caller,
                   "questions": questions}, f, indent=1)

    print(f"\nSaved {len(questions)} questions to {args.out}")
    for group in ["stranger", "hidden_real"]:
        qs = [q for q in questions if q["group"] == group]
        if not qs:
            continue
        print(f"  {group}: {len({q['syscall'] for q in qs})} syscalls, "
              f"{len({q['caller'] for q in qs})} different callers, "
              f"{sum(q['callee_source_missing'] for q in qs)} with callee source missing")
    longest = max(q["prompt_chars"] for q in questions)
    print(f"  longest prompt: {longest:,} characters (~{longest // 4:,} tokens)")


if __name__ == "__main__":
    main()
