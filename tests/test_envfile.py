"""src/kosis_agent/envfile.py: phase 3 loads .env, shell values win, KOSIS_DOTENV=off skips it."""

import os

from kosis_agent.envfile import load_env_file


def test_loads_without_overriding_the_shell(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("T323_A=from_file\nT323_B=from_file\n", encoding="utf-8")
    monkeypatch.setenv("KOSIS_DOTENV", "on")
    monkeypatch.setenv("T323_A", "from_shell")
    monkeypatch.delenv("T323_B", raising=False)
    assert load_env_file(env) == env
    assert os.environ["T323_A"] == "from_shell" and os.environ["T323_B"] == "from_file"
    monkeypatch.delenv("T323_B")


def test_off_and_missing_file_load_nothing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("T323_C=x\n", encoding="utf-8")
    monkeypatch.delenv("T323_C", raising=False)
    monkeypatch.setenv("KOSIS_DOTENV", "off")
    assert load_env_file(env) is None and "T323_C" not in os.environ
    monkeypatch.setenv("KOSIS_DOTENV", "on")
    assert load_env_file(tmp_path / "missing.env") is None


def test_run_eval_arguments_win_over_shell_and_env_file(tmp_path, monkeypatch):
    import argparse

    from evals.run_eval import SEARCH_ARGS, apply_settings

    env = tmp_path / ".env"
    env.write_text("CHROMA_COLLECTION=from_file\nRERANK_CKPT=from_file.pt\n", encoding="utf-8")
    monkeypatch.setenv("KOSIS_DOTENV", "on")
    monkeypatch.setenv("CHROMA_COLLECTION", "from_shell")  # monkeypatch restores both after the test
    monkeypatch.setenv("RERANK_CKPT", "from_shell.pt")
    monkeypatch.setenv("EMBED_CKPT", "from_shell.pt")
    values = {field: None for field in SEARCH_ARGS} | {"collection": "from_arg", "embed_ckpt": "base"}
    args = argparse.Namespace(env_file=env, no_env_file=False, **values)
    assert apply_settings(args) == env
    assert os.environ["CHROMA_COLLECTION"] == "from_arg" and os.environ["EMBED_CKPT"] == "base"
    assert os.environ["RERANK_CKPT"] == "from_shell.pt"  # no argument: the shell still beats .env
