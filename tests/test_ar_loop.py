import math
import subprocess

import pytest

from autoresearch import loop
from autoresearch.baselines import variant_source
from autoresearch.loop import (CAND, Ctx, changed_files, append_row, build_claude_cmd, git, read_rows, require_branch, research_round,
                               round0, top3, write_report)


@pytest.fixture
def git_repo(tmp_path):
    def sh(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    sh("init", "-b", "main")
    sh("config", "user.email", "test@example.com")
    sh("config", "user.name", "test")
    (tmp_path / "strategy").mkdir()
    (tmp_path / CAND).write_text("v0\n")
    (tmp_path / ".gitignore").write_text("autoresearch/runs/\n")
    sh("add", "-A")
    sh("commit", "-m", "init")
    sh("checkout", "-b", "autoresearch/test")
    return tmp_path


def make_ctx(repo):
    return Ctx(cwd=repo, run_dir=repo / "autoresearch" / "runs" / "test", run_id="test")


def test_build_claude_cmd():
    assert build_claude_cmd("x", None)[:3] == ["claude", "-p", "x"]
    assert "--model" not in build_claude_cmd("x", None)
    assert build_claude_cmd("x", "sonnet")[-2:] == ["--model", "sonnet"]


def test_require_branch_rejects_main(git_repo):
    assert require_branch(git_repo) == "autoresearch/test"
    git(git_repo, "checkout", "main")
    with pytest.raises(SystemExit):
        require_branch(git_repo)


def test_round_flow_keep_pass_discard(git_repo):
    ctx = make_ctx(git_repo)
    ctx.best_tag, ctx.best_score = "ar/test/base", 5
    git(git_repo, "tag", ctx.best_tag)
    cand = git_repo / CAND
    calls = []

    def edit(text, extra=False):
        def agent(cmd, cwd, log):
            cand.write_text(text)
            if extra:
                (git_repo / "other.py").write_text("x")
        return agent

    def ev(ret):
        def evaluator(cwd, out, *, baseline, seed):
            calls.append(seed)
            return {"status": "pass", "description": f"idea {seed}", "total_return": ret}
        return evaluator

    r1 = research_round(ctx, 1, edit("v1\n"), ev(10))
    assert r1["status"] == "keep"
    assert git(git_repo, "rev-parse", "HEAD") == git(git_repo, "rev-parse", "ar/test/r001")
    r2 = research_round(ctx, 2, edit("v2\n"), ev(7))
    assert r2["status"] == "pass"
    assert git(git_repo, "rev-parse", "ar/test/r002")
    assert git(git_repo, "rev-parse", "HEAD") == git(git_repo, "rev-parse", "ar/test/r001")
    assert cand.read_text() == "v1\n"
    r3 = research_round(ctx, 3, edit("v3\n", extra=True), ev(99))
    assert r3["status"] == "discard" and "touched" in r3["reason"]
    assert not (git_repo / "other.py").exists() and cand.read_text() == "v1\n"
    n_calls = len(calls)
    r4 = research_round(ctx, 4, lambda *a: None, ev(99))
    assert r4["reason"] == "no change" and len(calls) == n_calls
    assert git(git_repo, "rev-parse", "HEAD") == git(git_repo, "rev-parse", "ar/test/r001")
    rows = read_rows(ctx.run_dir / "results.tsv")
    assert [r["status"] for r in rows] == ["keep", "pass", "discard", "discard"]


def test_round0_starts_even_if_all_baselines_fail(git_repo):
    ctx = make_ctx(git_repo)
    vals = iter(range(1, 6))

    def evaluator(cwd, out, *, baseline, seed):
        return {"status": "fail", "reason": "dd", "total_return": next(vals)}

    round0(ctx, evaluator)
    assert ctx.best_score == -math.inf and ctx.best_tag.endswith("r000-martin30")
    assert (git_repo / CAND).read_text() == variant_source("martin30")


def test_keyboard_interrupt_resets_and_reports(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(loop.data, "load_all", lambda: None)
    git(git_repo, "tag", "ar/test/base")

    def fake_round0(ctx, evaluator=None):
        ctx.best_tag = ctx.baseline_tag = "ar/test/base"
        return []

    def fake_agent(cmd, cwd, log):
        (git_repo / CAND).write_text("dirty\n")
        raise KeyboardInterrupt

    monkeypatch.setattr(loop, "round0", fake_round0)
    monkeypatch.setattr(loop, "run_agent", fake_agent)
    assert loop.main(["--rounds", "1"]) == 130
    assert (git_repo / CAND).read_text() == "v0\n"
    assert (git_repo / "autoresearch/runs/test/report.md").exists()


def test_report_top3_sorted_and_flags(git_repo):
    ctx = make_ctx(git_repo)
    ctx.run_dir.mkdir(parents=True)
    rows = [{"round": i, "status": s, "reason": "", "description": f"d{i}", "total_return": t, "mdd": 0.25,
             "r": 0.04, "entry_tf": "1h", "version": f"ar/test/r{i:03d}"}
            for i, (s, t) in enumerate([("keep", 3), ("pass", 9), ("discard", 50), ("pass", 5), ("keep", 1)])]
    (ctx.run_dir / "r001.json").write_text('{"select_window": 200, "entry_tf": "1h", "top_n": 20}')
    assert [r["round"] for r in top3(rows)] == [1, 3, 0]
    text = write_report(ctx, rows).read_text()
    assert text.index("d1") < text.index("d3") < text.index("d0") and "d2" not in text
    assert "部署時實盤也要改成 200" in text


def _row(rnd, status, reason, total, version):
    return {"round": rnd, "status": status, "reason": reason, "total_return": total, "version": version,
            "description": "d", "mdd": 0.25, "r": 0.04, "entry_tf": "1h"}


def test_resume_prefers_passing_keep(git_repo):
    ctx = make_ctx(git_repo)
    rows = [_row("0", "keep", "", "2.0", "ar/test/r000-a"), _row("1", "keep", "", "9.0", "ar/test/r001")]
    assert loop.resume(ctx, rows) == 2
    assert (ctx.best_tag, ctx.best_score) == ("ar/test/r001", 9.0)
    ctx = make_ctx(git_repo)
    rows = [_row("0", "start", "dd", "5.0", "ar/test/r000-martin30")]
    assert loop.resume(ctx, rows) == 1
    assert ctx.best_tag == "ar/test/r000-martin30" and ctx.best_score == -math.inf
    assert top3(rows) == []


def test_round0_replaces_leftover_tags(git_repo):
    ctx = make_ctx(git_repo)
    git(git_repo, "tag", "ar/test/r000-rs_v1")
    round0(ctx, lambda cwd, out, *, baseline, seed: {"status": "pass", "total_return": 1})
    assert git(git_repo, "rev-parse", "HEAD") == git(git_repo, "rev-parse", ctx.best_tag)


def test_agent_timeout_discards_without_eval(git_repo):
    ctx = make_ctx(git_repo)
    ctx.best_tag = "ar/test/base"
    git(git_repo, "tag", ctx.best_tag)

    def agent(cmd, cwd, log):
        (git_repo / CAND).write_text("half\n")
        return False

    def ev(*a, **k):
        raise AssertionError("evaluator must not run")

    row = research_round(ctx, 1, agent, ev)
    assert row["status"] == "discard" and row["reason"] == "agent timeout"
    assert (git_repo / CAND).read_text() == "v0\n"


def test_main_refuses_stray_files(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    (git_repo / "other.txt").write_text("mine")
    assert loop.main(["--rounds", "1"]) == 1
    assert (git_repo / "other.txt").read_text() == "mine"


def test_main_missing_data_returns_1(git_repo, monkeypatch, capsys):
    monkeypatch.chdir(git_repo)

    def boom():
        raise FileNotFoundError("x")
    monkeypatch.setattr(loop.data, "load_all", boom)
    assert loop.main([]) == 1
    assert "python -m autoresearch.data" in capsys.readouterr().out


def test_round_error_becomes_discard_and_restores(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(loop.data, "load_all", lambda: None)
    git(git_repo, "tag", "ar/test/base")
    tsv = git_repo / "autoresearch/runs/test/results.tsv"
    append_row(tsv, {**_row("0", "keep", "", "1.0", "ar/test/base"), "seconds": "0.0"})
    monkeypatch.setattr(loop, "run_agent", lambda cmd, cwd, log: (git_repo / CAND).write_text("dirty\n"))

    def bad_eval(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(loop, "run_eval", bad_eval)
    assert loop.main(["--rounds", "1"]) == 0
    last = read_rows(tsv)[-1]
    assert last["status"] == "discard" and last["reason"].startswith("error: RuntimeError")
    assert (git_repo / CAND).read_text() == "v0\n" and not changed_files(git_repo)
