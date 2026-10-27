"""研究迴圈：round 0 跑 5 個 baseline，之後每輪由 `claude -p` 改 candidate，腳本評分、留或丟。"""
import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from autoresearch import data
from autoresearch.baselines import BASELINES, variant_source

CAND = "strategy/candidate.py"
COLUMNS = ["round", "description", "status", "reason", "r", "mdd", "total_return", "trades",
           "max_coin_dd", "win_rate", "profit_factor", "entry_tf", "version", "seconds"]
PROGRAM = Path(__file__).with_name("program.md")


@dataclass
class Ctx:
    cwd: Path
    run_dir: Path
    run_id: str
    best_tag: str | None = None
    best_score: float = -math.inf
    baseline_tag: str | None = None
    model: str | None = None
    rows: list = field(default_factory=list)  # results.tsv 以這份為準，每輪整檔重寫
    agent_fails: int = 0  # 連續幾輪 agent 逾時或非 0 結束


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.rstrip("\n")  # 不能 strip 前導空白：porcelain 第一欄可能是空格


def require_branch(cwd: Path) -> str:
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch.startswith("autoresearch/"):
        raise SystemExit(f"目前分支 {branch!r} 不是 autoresearch/ 開頭，迴圈會 reset 工作目錄，拒絕執行。\n"
                         "先建專用 worktree：git worktree add ../screener-ar -b autoresearch/2026-10-22")
    return branch


def changed_files(cwd: Path) -> set[str]:
    out = git(cwd, "status", "--porcelain", "-uall")
    return {line[3:].split(" -> ")[-1].strip('"') for line in out.splitlines() if line.strip()}


def ignored_files(cwd: Path) -> set[str]:
    return {p for p in git(cwd, "ls-files", "-o", "-i", "--exclude-standard", "-z").split("\0") if p}


def snapshot(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in d.rglob("*") if p.is_file()} if d.exists() else {}


def check_files(ctx: Ctx, ign0: set[str], run0: dict[str, bytes], own: set[str]) -> str | None:
    """agent 或評分之後：新的 ignored 檔刪掉；run_dir 裡 own 以外的檔被動過就照記憶體還原。回傳丟棄原因或 None。"""
    new = sorted(p for p in ignored_files(ctx.cwd) - ign0
                 if not p.startswith("autoresearch/runs/") and "__pycache__/" not in p)
    for p in new:
        (ctx.cwd / p).unlink(missing_ok=True)
    now = snapshot(ctx.run_dir)
    touched = {k for k in now.keys() | run0.keys() if k not in own and now.get(k) != run0.get(k)}
    for k in touched:
        if k in run0:
            (ctx.run_dir / k).parent.mkdir(parents=True, exist_ok=True)
            (ctx.run_dir / k).write_bytes(run0[k])
        else:
            (ctx.run_dir / k).unlink()
    if new:
        return f"touched: {', '.join(new)}"
    return "touched run files" if touched else None


def tree_reason(cwd: Path) -> str | None:
    files = changed_files(cwd)
    if files != {CAND}:
        others = sorted(files - {CAND})
        return f"touched: {', '.join(others)}" if others else "no change"
    return None if (cwd / CAND).exists() else "candidate deleted"


def build_claude_cmd(prompt: str, model: str | None) -> list[str]:
    # claude --help 已確認 -p、--safe-mode、--permission-mode、--tools、--disallowedTools、--model 都存在；
    # --safe-mode 不載入使用者的 CLAUDE.md／plugins／hooks／MCP，--tools 只留這四個工具
    cmd = ["claude", "-p", prompt, "--safe-mode", "--permission-mode", "acceptEdits", "--tools", "Read,Edit,Glob,Grep",
           "--disallowedTools", "Bash,Write,WebFetch,WebSearch,Task"]
    return cmd + ["--model", model] if model else cmd


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return -math.inf


def fmt(v, nd=4) -> str:
    return "-" if v in (None, "") else f"{num(v):.{nd}f}"


def build_prompt(program: str, rows: list[dict], ctx: Ctx) -> str:
    best = next((r for r in rows if r.get("version") == ctx.best_tag), None)
    if best:
        title = "起點（未通過）" if best["status"] == "start" else "目前第 1 名"
        head = (f"## {title}\n版本 {best['version']}；說明：{best['description']}；總報酬 {fmt(best['total_return'], 2)}；"
                f"回撤 {fmt(best['mdd'])}；r {fmt(best['r'])}；entry_tf {best['entry_tf']}")
    else:
        head = "## 目前第 1 名\n（無）"
    hist = "\n".join(f"{r['round']}\t{r['status']}\t{r['reason']}\t{fmt(r['total_return'], 2)}\t{r['description']}"
                     for r in rows[-15:])
    return f"{program}\n\n{head}\n\n## 最近 15 列（round / status / reason / total_return / description）\n{hist}\n"


def run_agent(cmd: list[str], cwd: Path, log_path: Path, timeout: int = 900) -> str | None:
    """輸出只寫 log 不解析。回傳 None = 正常結束；否則是丟棄原因（agent timeout／agent exit N）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as f:
        try:
            p = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            f.write(b"\n[autoresearch] agent timeout\n")
            return "agent timeout"
    return f"agent exit {p.returncode}" if p.returncode else None


def run_eval(cwd: Path, out: Path, *, baseline: bool, seed: int, timeout: int = 3600) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)  # 舊檔不能被誤當成這輪結果
    cmd = [sys.executable, "-m", "autoresearch.score", "--out", str(out), "--seed", str(seed)]
    if baseline:
        cmd.append("--baseline")
    with open(out.with_suffix(".score.log"), "wb") as log:  # rNNN.json -> rNNN.score.log
        try:
            subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "fail", "reason": "timeout"}
    try:
        return json.loads(out.read_text())
    except (OSError, ValueError):
        return {"status": "fail", "reason": "no output"}


def read_rows(tsv: Path) -> list[dict]:
    if not tsv.exists():
        return []
    with open(tsv, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def write_rows(tsv: Path, rows: list[dict]) -> None:
    """整檔重寫（記憶體裡的 rows 才是準的，檔案被改過也會蓋回來）。"""
    tsv.parent.mkdir(parents=True, exist_ok=True)
    tmp = tsv.with_name(tsv.name + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(COLUMNS)
        for row in rows:
            w.writerow(["" if row.get(c) is None else str(row.get(c)).replace("\t", " ").replace("\n", " ")
                        for c in COLUMNS])
    os.replace(tmp, tsv)


def make_row(n: int, res: dict, status: str, reason: str, version: str, seconds: float) -> dict:
    row = {c: res.get(c) for c in COLUMNS}
    row.update(round=n, status=status, reason=reason, version=version, seconds=f"{seconds:.1f}",
               description=res.get("description") or "")
    return row


def top3(rows: list[dict]) -> list[dict]:
    ok = [r for r in rows if r["status"] in ("keep", "pass")]
    return sorted(ok, key=lambda r: num(r["total_return"]), reverse=True)[:3]


def write_report(ctx: Ctx, rows: list[dict]) -> Path:
    lines = [f"# Autoresearch 報告 {ctx.run_id}", ""]
    for i, r in enumerate(top3(rows), 1):
        tag = r["version"]
        lines += [f"## 第 {i} 名：{tag}", "", f"- 說明：{r['description']}",
                  f"- 總報酬：{fmt(r['total_return'], 2)}；回撤：{fmt(r['mdd'])}；r：{fmt(r['r'])}；entry_tf：{r['entry_tf']}"]
        jp = ctx.run_dir / (tag.split("/")[-1] + ".json")
        js = json.loads(jp.read_text()) if jp.exists() else {}
        if js.get("fee") is not None:
            lines.append(f"- 手續費（單邊）：{js['fee']}")
        for key, default, label in (("select_window", 300, "SELECT_WINDOW_4H"), ("entry_tf", "1h", "ENTRY_TF"),
                                    ("top_n", 20, "TOP_N")):
            if js.get(key) is not None and js[key] != default:
                lines.append(f"- 部署時實盤也要改成 {js[key]}（{label}，預設 {default}）")
        if js.get("yearly"):
            lines += ["", "| 年 | 報酬 | 回撤 |", "|---|---|---|"]
            lines += [f"| {y['year']} | {fmt(y['return'], 2)} | {fmt(y['mdd'])} |" for y in js["yearly"]]
        if js.get("top_coins"):
            lines += ["", "獲利最多的幣：" + "、".join(f"{s} {fmt(p, 2)}" for s, p in js["top_coins"][:3])]
        if ctx.baseline_tag:
            diff = git(ctx.cwd, "diff", ctx.baseline_tag, tag, "--", CAND)
            lines += ["", "```diff", diff, "```"]
        lines.append("")
    path = ctx.run_dir / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    return path


def tag_name(ctx: Ctx, suffix: str) -> str:
    return f"ar/{ctx.run_id}/{suffix}"


def round0(ctx: Ctx, evaluator=None) -> list[dict]:
    evaluator = evaluator or run_eval
    rows = []
    for old in git(ctx.cwd, "tag", "-l", tag_name(ctx, "r000-*")).splitlines():
        git(ctx.cwd, "tag", "-d", old)  # round 0 只在沒有 tsv 時跑，殘留的 tag 是上次被中斷的
    for sel in BASELINES:
        t0 = time.monotonic()
        (ctx.cwd / CAND).write_text(variant_source(sel))
        res = evaluator(ctx.cwd, ctx.run_dir / f"r000-{sel}.json", baseline=True, seed=new_seed())
        git(ctx.cwd, "add", CAND)
        git(ctx.cwd, "commit", "--allow-empty", "-m", f"ar r000 {sel}")
        tag = tag_name(ctx, f"r000-{sel}")
        git(ctx.cwd, "tag", tag)
        ok = res.get("status") == "pass"
        rows.append(make_row(0, res, "pass" if ok else "fail", res.get("reason") or ("" if ok else "fail"), tag,
                             time.monotonic() - t0))
    passed = [r for r in rows if r["status"] == "pass"]
    best = max(passed or rows, key=lambda r: num(r["total_return"]))
    best["status"] = "keep" if passed else "start"  # 全 fail 時只是起點，不是通過的版本
    ctx.best_tag = ctx.baseline_tag = best["version"]
    ctx.best_score = num(best["total_return"]) if passed else -math.inf
    git(ctx.cwd, "reset", "--hard", ctx.best_tag)
    ctx.rows = rows  # 由 main 決定要不要寫進 results.tsv
    return rows


def new_seed() -> int:
    return int.from_bytes(os.urandom(4))  # 前綴檢查的切點不能被 agent 預先知道；JSON 會記下來，可用 --seed 重現


def research_round(ctx: Ctx, n: int, agent=None, evaluator=None) -> dict:
    agent = agent or run_agent
    evaluator = evaluator or run_eval
    t0 = time.monotonic()
    ign0, run0 = ignored_files(ctx.cwd), snapshot(ctx.run_dir)
    own = {f"agent_r{n:03d}.log"}
    prompt = build_prompt(PROGRAM.read_text(), ctx.rows, ctx)
    fail = agent(build_claude_cmd(prompt, ctx.model), ctx.cwd, ctx.run_dir / f"agent_r{n:03d}.log")
    ctx.agent_fails = ctx.agent_fails + 1 if fail else 0
    touched = check_files(ctx, ign0, run0, own)  # 不論 agent 成敗都要跑：清掉 ignored 檔、還原 run 檔
    reason = fail or touched or tree_reason(ctx.cwd)
    res, version = {}, ""
    if reason is None:
        src = (ctx.cwd / CAND).read_bytes()
        res = evaluator(ctx.cwd, ctx.run_dir / f"r{n:03d}.json", baseline=False, seed=new_seed())
        own |= {f"r{n:03d}.json", f"r{n:03d}.score.log"}
        reason = (check_files(ctx, ign0, run0, own) or tree_reason(ctx.cwd)
                  or (f"touched: {CAND} during scoring" if (ctx.cwd / CAND).read_bytes() != src else None)
                  or (None if res.get("status") == "pass" else res.get("reason") or "fail"))
    if reason is None:
        git(ctx.cwd, "add", CAND)
        git(ctx.cwd, "commit", "-m", f"ar r{n:03d}: {res.get('description') or ''}")
        version = tag_name(ctx, f"r{n:03d}")
        git(ctx.cwd, "tag", "-f", version)
        status, reason = "pass", res.get("reason") or ""
        if res["total_return"] > ctx.best_score:
            status = "keep"
            ctx.best_tag, ctx.best_score = version, res["total_return"]
    else:
        status = "discard"
        git(ctx.cwd, "checkout", "--", ".")
        git(ctx.cwd, "clean", "-fd")  # 不加 -x：autoresearch/runs/ 要留著；新的 ignored 檔 check_files 已刪
    if status != "keep":
        git(ctx.cwd, "reset", "--hard", ctx.best_tag)
    row = make_row(n, res, status, reason, version, time.monotonic() - t0)
    ctx.rows.append(row)
    write_rows(ctx.run_dir / "results.tsv", ctx.rows)
    write_report(ctx, ctx.rows)
    return row


def resume(ctx: Ctx, rows: list[dict]) -> int:
    """從 results.tsv 還原狀態，回傳下一輪編號。"""
    keeps = [r for r in rows if r["status"] == "keep"]
    if keeps:
        best = max(keeps, key=lambda r: num(r["total_return"]))
        ctx.best_score = num(best["total_return"])
    else:  # round 0 全 fail：起點是 "start" 列，沒有分數
        best = next(r for r in rows if r["status"] == "start")
        ctx.best_score = -math.inf
    ctx.best_tag = best["version"]
    ctx.baseline_tag = next(r["version"] for r in rows if r["round"] == "0" and r["status"] in ("keep", "start"))
    return int(rows[-1]["round"]) + 1


def restore(ctx: Ctx) -> None:
    git(ctx.cwd, "checkout", "--", ".")
    git(ctx.cwd, "reset", "--hard", ctx.best_tag)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)
    cwd = Path.cwd()
    branch = require_branch(cwd)
    run_id = branch.removeprefix("autoresearch/")
    stray = changed_files(cwd) - {CAND}
    if stray:
        print("工作目錄有其他未提交的檔案，請先 commit 或移除（迴圈不會動它們）：\n  " + "\n  ".join(sorted(stray)))
        return 1
    ctx = Ctx(cwd=cwd, run_dir=cwd / "autoresearch" / "runs" / run_id, run_id=run_id, model=args.model)
    if not data.cache_ready():
        print("缺資料，先執行 `python -m autoresearch.data`")
        return 1
    tsv = ctx.run_dir / "results.tsv"
    try:
        ctx.rows = read_rows(tsv)
        if ctx.rows:
            start = resume(ctx, ctx.rows)
            restore(ctx)  # 上次中斷可能留下髒的 candidate 或偏離 best 的 HEAD
        else:
            round0(ctx)
            if ctx.rows and all(_broken(r["reason"]) for r in ctx.rows):
                print("round 0 的 5 個 baseline 全部 error／timeout／no output：這是 harness 或資料問題，不是策略問題。\n"
                      f"看 {ctx.run_dir}/r000-*.score.log，修好再重跑（results.tsv 沒寫，會重跑 round 0）。")
                print(write_report(ctx, ctx.rows))
                return 2
            write_rows(tsv, ctx.rows)
            start = 1
        for n in range(start, start + args.rounds):
            try:
                research_round(ctx, n)
            except Exception as e:  # 單輪出錯不擋整個迴圈；KeyboardInterrupt 不在此列
                restore(ctx)
                ctx.rows.append(make_row(n, {}, "discard", f"error: {type(e).__name__}: {e}"[:200], "", 0.0))
                write_rows(tsv, ctx.rows)
            if ctx.agent_fails >= 3:
                print(f"agent 連續 {ctx.agent_fails} 輪逾時或非 0 結束，停止。看 {ctx.run_dir}/agent_r*.log "
                      "（claude 登入、額度、網路）。")
                print(write_report(ctx, ctx.rows))
                return 2
    except KeyboardInterrupt:
        if ctx.best_tag:
            git(cwd, "checkout", "--", ".")
            git(cwd, "clean", "-fd")
            git(cwd, "reset", "--hard", ctx.best_tag)
        print(write_report(ctx, ctx.rows))
        return 130
    print(write_report(ctx, ctx.rows))
    return 0


def _broken(reason: str) -> bool:
    return reason.startswith("error:") or reason in ("timeout", "no output")


if __name__ == "__main__":
    sys.exit(main())
