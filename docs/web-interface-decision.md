# Web Interface Decision

## Decision Context

The approved gate asks for at least four weeks of actual use, or a shorter
evaluation explicitly approved by the user. Neither evidence source exists as
of 2026-07-13. This report therefore evaluates whether the repository currently
contains enough evidence to authorize a web specification; it does not treat
implemented capabilities as proof of user demand.

## Evidence Labels

- **Observed**: directly verifiable in the repository or its automated tests.
- **Unknown**: requires real-use observations that have not been collected.
- **Speculation**: a plausible benefit or problem with no measured example;
  speculation receives no positive score.

## Scoring Rule

Each factor is scored from 0 to 2:

- `0`: current evidence does not justify web work, or a prerequisite is absent;
- `1`: the need is unknown or mixed and requires observation;
- `2`: repeated observed failures show that a web interface is needed.

Writing a web specification requires at least 8 of 12 points, including a
confirmed maintenance budget and an approved remote-access security model.
This deliberately makes missing evidence a reason to gather evidence, not a
reason to invent requirements.

## Rubric

| Factor | Score | Evidence class | Evidence and interpretation |
| --- | ---: | --- | --- |
| Cross-device access need | 1/2 | Unknown | No usage log, interview, or approved evaluation records a failed cross-device task. A browser could provide cross-device access, but that is Speculation rather than observed demand. |
| Obsidian review workflow gap | 1/2 | Unknown | The repository implements and tests deterministic Vault export and daily review projections in [`tests/test_wiki_cli.py`](../tests/test_wiki_cli.py) and [`tests/test_daily_review.py`](../tests/test_daily_review.py). These are capability observations, not proof that the workflow succeeds or fails in actual use. No recurring Obsidian failure has been recorded. |
| Visual relation review need | 1/2 | Unknown | Relation generation and bounded candidate selection are tested in [`tests/test_relation_pipeline.py`](../tests/test_relation_pipeline.py) and [`tests/test_relation_candidates.py`](../tests/test_relation_candidates.py), but no observed review session shows that text or Vault navigation is inadequate. A graph canvas might help, but that is Speculation. |
| Nontechnical user need | 1/2 | Unknown | CLI and local MCP interfaces are implemented and tested in [`tests/test_cli.py`](../tests/test_cli.py) and [`tests/test_mcp_interface.py`](../tests/test_mcp_interface.py). There is no approved participant list or observed nontechnical user who was blocked by either interface. |
| Maintenance budget | 0/2 | Observed | No owner, time allocation, deployment target, browser support policy, or maintenance budget is recorded in the repository. An unspecified web application would add an unowned operational surface. |
| Remote-access security implications | 0/2 | Observed | The current MCP interface is constrained to local stdio and explicitly avoids a remote listener, as tested in [`tests/test_mcp_interface.py`](../tests/test_mcp_interface.py). No authentication, authorization, TLS termination, exposure boundary, or incident-recovery design has been approved for remote web access. |

**Total: 4/12.** The score is below the threshold, and both mandatory
prerequisites are absent.

## Observed Examples and Missing Evidence

The repository demonstrates that local search, review projection, relation
generation, and a local automation interface exist. It does not demonstrate
four weeks of daily use, abandoned review attempts, cross-device failures,
requests from nontechnical users, or time saved by a visual relation tool.
Those missing measurements remain Unknown. They must not be backfilled with
feature ideas.

Before reconsideration, record dated workflow attempts for four weeks (or run a
user-approved shorter evaluation), including device, task, chosen interface,
completion or abandonment, friction, and workaround. Re-score this same rubric
from those observations. If the threshold and both prerequisites are met, begin
a separate brainstorming and web-specification cycle; do not infer frontend
requirements from this report.

decision: defer
