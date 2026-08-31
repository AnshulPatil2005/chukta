# Architecture decision records

One page per significant choice: what was picked, what was rejected, and why.
The rejected alternatives are the useful half - they are what stops the same
argument being relitigated in a week.

| ADR | Decision |
| --- | --- |
| [0001](0001-incremental-not-gross.md) | Report incremental recovery, not gross recovery rate |
| [0002](0002-two-tier-taxonomy.md) | Two-tier failure classification with a visible confidence level |
| [0003](0003-common-random-numbers.md) | Common random numbers across arms |
| [0004](0004-no-live-api.md) | No live Razorpay integration |
| [0005](0005-gates-before-execution.md) | Compliance checked before execution, per action |
| [0006](0006-uplift-threshold.md) | Suppress outreach below a measured uplift threshold |
| [0007](0007-service-not-transactional.md) | Recovery notices are Service messages, not Transactional |
| [0008](0008-model-writes-only.md) | The model writes copy and nothing else |
| [0009](0009-live-demo-path.md) | A live demo path, with measurement still credential-free |

`JOURNAL.md` is the chronological record of what went wrong and when. These are
the decisions that came out of it, stated once and properly.
