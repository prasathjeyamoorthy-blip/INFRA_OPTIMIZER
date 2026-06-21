# Product — Dynamic Infrastructure Cost Optimizer Agent

An autonomous AWS cost-optimization system that runs a continuous observe–decide–act loop. It monitors live EC2 instances, calls Claude (the LLM) once per cycle to decide what infrastructure actions to take, executes real boto3 calls, and persists all decisions to a SQLite database. A Textual terminal dashboard polls the REST API every 3 seconds to display live state.

## Core value proposition
- Eliminates manual EC2 cost-optimization decisions
- The LLM is the sole decision-maker — no hardcoded if/else action logic anywhere
- All actions are audited, with full reasoning stored per cycle
- A kill switch halts execution within one cycle without restarting any process

## Four EC2 instances managed
| Name | protected tag |
|---|---|
| web-1 | false |
| web-2 | false |
| worker-1 | false |
| cache-1 | **true** — never acted upon |

All instances are tagged `project=cost-optimizer-agent`. The agent only ever touches instances with this tag.
