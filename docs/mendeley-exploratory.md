# Duebook on two Mendeley help-desk tickets

The publisher-masked public [Help Desk Tickets v3 release](https://doi.org/10.17632/btm76zndnt.3) supplied two tickets. M1 had 142 visible comments and M2 had 134.
Withholding all `others` comments left 102 inputs for M1 (40 withheld) and 118 for M2 (16 withheld).
The run used default Guards (rule version 6) and a Sunday–Thursday `Asia/Amman` calendar. Both tickets had zero visible comments on Friday and Saturday in UTC+3.
CC BY 4.0 with additional masking. Inference used OpenRouter with ZDR and data collection denied, pinned to DeepInfra FP8.

## M1 · scanning failures

- At handoff (workflow `1049267.0`, after comment `1052058.0`), the card held one fact about the multi-branch error and an open request to fix it (`211718553.0`). The promise list was empty.
- Duebook recorded the first promise to report back (`1057814.0`) and opened three more on restatements (`1072867.0`, `1075412.0`, `1076270.0`).
  All four stayed open after support supplied a test fix and the customer responded (`1081194.0`, `1081201.0`). The draft treats the follow-up as fulfilled.
- The reducer rejected the workstation follow-up at `1081988.0` as a duplicate and admitted the later restatement at `1082599.0`.
  The reader treated the July 28 10:30 appointment as a question and missed the promise and its deadline (`1082616.0`, `1082629.0`).
  It also missed the commitment to postpone testing and use the new scan page (`1112421.0`).
- The draft marks three follow-ups or connections as claimed after support reported deployment or installation. The card kept them open
  (`1081665.0` → `1081904.0`, `1088227.0` → `1090536.0`, `1094402.0` → `1094693.0`). Duebook also opened a second connection promise at `1088835.0`.
- Duebook combined the customer's retest and reporting commitments (`1097773.0`) and left them open after the customer posted the scan error and support responded (`1098635.0`, `1099130.0`).
  All 11 commitments stayed open through the end. False closes totaled zero.
- The September 18 noon appointment (`1092403.0`) resolved to 09:00 UTC with high confidence and raised a due-soon warning at 07:23 UTC and a breach at 09:00 UTC.
  The next eligible comment asks for news (`1092949.0`). ASAP promises stayed undated (`1057814.0`, `1081665.0`, `1088227.0`).

## M2 · recurring invalid files

- At handoff (workflow `1068688.0`, after comment `1072996.0`), the card held the fact that invalid incoming files appeared daily (`211720447.0`). The promise list was empty.
- Duebook kept the investigation follow-up (`1074156.0`), added a duplicate (`1076269.0`), and combined the design update and expected fix in one undated promise (`1082536.0`).
  The initial follow-up stayed open after support identified the cause, scheduled a fix, and the customer tested it (`1082536.0`, `1096529.0`, `1096906.0`).
- The test deployment promise (`1096529.0`) resolved to Oct 16 at 20:59:59 UTC, with medium confidence, from “next Sunday.”
  Support reported deployment Oct 9 and the customer confirmed successful tests Oct 10 (`1096670.0`, `1096906.0`).
  The report was attached to the connection promise and the customer's confirmation targeted an unknown commitment. The deployment raised a due-soon warning and a false breach.
- The Oct 9 11:00 local test connection (`1096653.0`) resolved to 08:00 UTC with high confidence.
  It raised due-soon and breach notices, then an unconfirmed-claim notice when the deployment report was admitted as a claim on the connection (`1096670.0`).
- Duebook missed the production deployment promise (`1098623.0`). It recorded the delivery as a fact and refused a close aimed at the test deployment (`1098793.0`).
  The production connection (`1098629.0`) resolved to Oct 18 at 11:30 UTC with medium confidence. The reader extracted no fulfillment from the link posted at 11:21 UTC (`1098775.0`), so the card raised due-soon and false-breach notices.
- “Until tomorrow” (`1098793.0`) and “end of the day” (`1098897.0`) both resolved to Oct 19 at 20:59:59 UTC with high confidence. The card raised two due-soon warnings and two breaches.
  The customer had already reported four recurring files (`1098890.0`), and support's request to keep monitoring was recorded as its own separate obligation (`1098897.0`).
- Customer monitoring and reporting became one promise (`1099526.0`). “End of this week” resolved to that same Sunday, Oct 23 at 20:59:59 UTC, because the resolver treated Sunday as the last workday of a Monday-based week.
  The promise stayed in breach after the customer reported two more files and support asked for logs (`1100339.0`, `1100350.0`). The card caught the new promise to preserve the files (`1100339.0`).
  The default alert policy suppressed notices for the customer deadline (`1099526.0`).
- Duebook kept the reopened investigation follow-up (`1100373.0`) open after a second test fix and successful retest (`1108290.0`, `1108812.0`).
  The final monitoring promise and conditional rollout (`1109645.0`) stayed pending at the final comment (`1110782.0`). Their medium-confidence cutoff was Dec 18 at 20:59:59 UTC, beyond observation.
  The card ended with 11 open or breached commitments and one claim. False closes totaled zero.

## Draft V1 scores (machine-drafted, not human-reviewed, n=2)

| Ticket | Matched / extra / missed | Precision / recall / F1 | Deadline right / judged | Status right / judged |
| --- | ---: | ---: | ---: | ---: |
| M1 | 6 / 5 / 4 | 0.545 / 0.600 / 0.571 | 1 / 1 | 0 / 4 |
| M2 | 10 / 2 / 3 | 0.833 / 0.769 / 0.800 | 1 / 1 | 1 / 6 |
| Total | 16 / 7 / 7 | 0.696 / 0.696 / 0.696 | 2 / 2 | 1 / 10 |

V1 scores the final cards by source anchor and owing side. Both judged deadlines were correctly absent. The deadline problems above sit outside that denominator.
Notices are derived from saved cards at observed event times and intervening scheduled wakeups.

## Cost and latency

One run completed in 408.7 s with 233 calls (220 extraction, 13 verification) and zero unread comments. Ledger spend was $0.0273770336. OpenRouter key usage increased by $0.027376928.
M1 used $0.0126526736 and 195.2 model seconds. M2 used $0.0147243600 and 212.7 model seconds.
