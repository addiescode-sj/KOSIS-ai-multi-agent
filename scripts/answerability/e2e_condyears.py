"""e2e graph for the best L1 setting (KOS-192): the service graph with cond_scope="years".

The service config (backend/app/config.py) has no variable for cond_scope, so backend.app.main:build_agent always
filters by every condition. This builder changes only that one option; Chroma, checkpoints, STAGE2 and phase 1 are
the service's settings from the shell / .env, as in a plain e2e run.

    python -m evals.run_eval --layer e2e --agent scripts.answerability.e2e_condyears:build --interval 5 --stage <name>
"""

from __future__ import annotations


def build():
    from backend.app import config, main

    base = config.agent_config

    def years():
        cfg = base()
        return cfg._replace(options=cfg.options.model_copy(update={"cond_scope": "years"}))

    config.agent_config = years  # main._build imports agent_config from the module when it runs
    try:
        return main.build_agent()
    finally:
        config.agent_config = base
