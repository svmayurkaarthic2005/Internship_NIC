# P3.4 live turns (real server on 127.0.0.1:8001, real DB, real JWT)

Server-side columns come from the server log of each request: `historical_ref` (prior pair, current, resolved IDs or clarification), `entity_context` (focus / pair the turn resolved against) and `entity_gate` (verdict and application before retrieval).


## normal

| # | question | client hist (ids) | historical ref → | server focus / pair | gate | IDs in answer | handler | latency | answer |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Compare 2026/0153/02/045470 and 2026/0153/02/045472. | 0  | - | - / - | ok 045470 | 045470,045472 | compare_applications | 0.48s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |
| 2 | Show the status of 2026/0154/02/012766. | 2 045470,045472 | - | - / 045470,045472 | ok 012766 | 012766 | application_status | 0.66s | The Status for 2026/0154/02/012766 is: approved |
| 3 | Compare 2026/0154/02/012766 with the first application from our earlier comparison. | 4 045470,045472,012766 | 012766,045470 | 012766 / 012766 | ok 012766 | 012766,045470 | compare_applications | 0.3s | **2026/0154/02/012766** vs **2026/0153/02/045470** • **Type**: ISD / NISD • **Status**: approved / rejected • **Filed**: |
| 4 | What is the status of the second application from our earlier comparison? | 6 045470,045472,012766 | 045472 | - / 012766,045470 | ok 045472 | 045472 | application_status | 0.48s | The Status for 2026/0153/02/045472 is: rejected |
| 5 | What about the other one? | 8 045470,045472,012766 | - | 045472 / 045472 | - | 045472,045470 | followup_clarification | 0.29s | Which application do you mean? You are now on 2026/0153/02/045472; the earlier comparison was 2026/0153/02/045470 and 20 |
| 6 | Show both applications from our previous comparison again. | 10 045470,045472,012766 | 045470,045472 | 045472 / 045472 | ok 045470 | 045470,045472 | compare_applications | 0.26s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |

## none

| # | question | client hist (ids) | historical ref → | server focus / pair | gate | IDs in answer | handler | latency | answer |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Compare 2026/0153/02/045470 and 2026/0153/02/045472. | 0  | - | - / - | ok 045470 | 045470,045472 | compare_applications | 0.2s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |
| 2 | Show the status of 2026/0154/02/012766. | 0  | - | - / 045470,045472 | ok 012766 | 012766 | application_status | 0.27s | The Status for 2026/0154/02/012766 is: approved |
| 3 | Compare 2026/0154/02/012766 with the first application from our earlier comparison. | 0  | 012766,045470 | 012766 / 012766 | ok 012766 | 012766,045470 | compare_applications | 0.27s | **2026/0154/02/012766** vs **2026/0153/02/045470** • **Type**: ISD / NISD • **Status**: approved / rejected • **Filed**: |
| 4 | What is the status of the second application from our earlier comparison? | 0  | 045472 | - / 012766,045470 | ok 045472 | 045472 | application_status | 0.42s | The Status for 2026/0153/02/045472 is: rejected |
| 5 | What about the other one? | 0  | - | 045472 / 045472 | - | 045472,045470 | followup_clarification | 0.26s | Which application do you mean? You are now on 2026/0153/02/045472; the earlier comparison was 2026/0153/02/045470 and 20 |
| 6 | Show both applications from our previous comparison again. | 0  | 045470,045472 | 045472 / 045472 | ok 045470 | 045470,045472 | compare_applications | 0.26s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |

## stale

| # | question | client hist (ids) | historical ref → | server focus / pair | gate | IDs in answer | handler | latency | answer |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Compare 2026/0153/02/045470 and 2026/0153/02/045472. | 2 012766,999999 | - | - / - | ok 045470 | 045470,045472 | compare_applications | 0.21s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |
| 2 | Show the status of 2026/0154/02/012766. | 2 012766,999999 | - | - / 045470,045472 | ok 012766 | 012766 | application_status | 0.22s | The Status for 2026/0154/02/012766 is: approved |
| 3 | Compare 2026/0154/02/012766 with the first application from our earlier comparison. | 2 012766,999999 | 012766,045470 | 012766 / 012766 | ok 012766 | 012766,045470 | compare_applications | 0.24s | **2026/0154/02/012766** vs **2026/0153/02/045470** • **Type**: ISD / NISD • **Status**: approved / rejected • **Filed**: |
| 4 | What is the status of the second application from our earlier comparison? | 2 012766,999999 | 045472 | - / 012766,045470 | ok 045472 | 045472 | application_status | 0.42s | The Status for 2026/0153/02/045472 is: rejected |
| 5 | What about the other one? | 2 012766,999999 | - | 045472 / 045472 | - | 045472,045470 | followup_clarification | 0.26s | Which application do you mean? You are now on 2026/0153/02/045472; the earlier comparison was 2026/0153/02/045470 and 20 |
| 6 | Show both applications from our previous comparison again. | 2 012766,999999 | 045470,045472 | 045472 / 045472 | ok 045470 | 045470,045472 | compare_applications | 0.25s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |

## stream_stale

| # | question | client hist (ids) | historical ref → | server focus / pair | gate | IDs in answer | handler | latency | answer |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Compare 2026/0153/02/045470 and 2026/0153/02/045472. | 2 012766,999999 | - | - / - | ok 045470 | 045470,045472 | compare_applications | 0.18s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |
| 2 | Show the status of 2026/0154/02/012766. | 2 012766,999999 | - | - / 045470,045472 | ok 012766 | 012766 | application_status | 0.23s | The Status for 2026/0154/02/012766 is: approved |
| 3 | Compare 2026/0154/02/012766 with the first application from our earlier comparison. | 2 012766,999999 | 012766,045470 | 012766 / 012766 | ok 012766 | 012766,045470 | (stream) | 0.29s | **2026/0154/02/012766** vs **2026/0153/02/045470** • **Type**: ISD / NISD • **Status**: approved / rejected • **Filed**: |
| 4 | What is the status of the second application from our earlier comparison? | 2 012766,999999 | 045472 | - / 012766,045470 | ok 045472 | 045472 | (stream) | 0.4s | The Status for 2026/0153/02/045472 is: rejected |
| 5 | What about the other one? | 2 012766,999999 | - | 045472 / 045472 | - | 045472,045470 | (stream) | 0.3s | Which application do you mean? You are now on 2026/0153/02/045472; the earlier comparison was 2026/0153/02/045470 and 20 |
| 6 | Show both applications from our previous comparison again. | 2 012766,999999 | 045470,045472 | 045472 / 045472 | ok 045470 | 045470,045472 | (stream) | 0.27s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |

## both_three

| # | question | client hist (ids) | historical ref → | server focus / pair | gate | IDs in answer | handler | latency | answer |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Compare 2026/0153/02/045470 and 2026/0153/02/045472. | 2 012766,999999 | - | - / - | ok 045470 | 045470,045472 | compare_applications | 0.19s | **2026/0153/02/045470** vs **2026/0153/02/045472** • **Type**: NISD / NISD • **Status**: rejected / rejected • **Filed** |
| 2 | Show the status of 2026/0154/02/012766. | 2 012766,999999 | - | - / 045470,045472 | ok 012766 | 012766 | application_status | 0.22s | The Status for 2026/0154/02/012766 is: approved |
| 3 | Compare 2026/0154/02/012766 with both applications from our previous comparison. | 2 012766,999999 | clarify | (answered before context load) | - | 012766,045470,045472 | clarification_required | 0.04s | I can compare two applications at a time. Compare 2026/0154/02/012766 with which one from the earlier comparison: 2026/0 |

## fresh

| # | question | client hist (ids) | historical ref → | server focus / pair | gate | IDs in answer | handler | latency | answer |
|---|---|---|---|---|---|---|---|---|---|
| 1 | What is the status of the second application from our earlier comparison? | 2 012766,999999 | clarify | (answered before context load) | - | - | clarification_required | 0.04s | There is no earlier comparison in this conversation to refer to. Give the application numbers. |
