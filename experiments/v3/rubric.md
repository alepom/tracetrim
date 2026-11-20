# Answer-quality rubric

Read the complete task, answer and original reference tool returns. Score the whole answer on three integer scales. Evaluate the relationship between each claim and its customer, invoice, run, log or issue. An identifier that exists somewhere in the packet cannot establish a claim about a different entity. Judge what is present in the answer. Accept a paraphrase where the task does not require an exact quotation. Keep each customer's reply free of technical identifiers.

| Score | Correctness and attribution | Coverage of the requested work | Explanation and next action |
|---|---|---|---|
| 0 | No usable answer or the central findings are contradicted throughout. | No verifiable requirement answered. | No usable explanation or action. |
| 1 | Most material findings are false or attached to another entity. | Less than half the material items are covered. | The proposed action generally conflicts with the evidence. |
| 2 | Some findings are correct, but at least one material attribution or conclusion is wrong. | At least half the material items are covered, with a material omission. | Partly useful, with a material gap or a harmful recommendation in one item. |
| 3 | The conclusions are supported with a minor imprecision. | All material items covered with a minor omission. | Usable explanation and actions with minor ambiguity. |
| 4 | All material findings and associations are supported, including stated uncertainty. | Every required item is addressed. | Evidence leads to the appropriate next action or a justified decision to take none. |

**Billing checklist.** For each of the five tickets establish the correct customer and applicable invoice(s), the task-specific adjustment or event and the requested amount, balance, quantity or date. Reconcile the figures with line items and settled payments. Distinguish void and collectible documents. Give a customer-facing answer and a supported next step. An instruction to pay an already settled invoice is material.

**CI checklist.** Cover each failed run on the requested branch and week. Group by cause, connect the run, job and step, quote the exact diagnostic line, match relevant open issues and changes, and say which action is still needed. A missing cause, attaching the wrong change, or claiming a fix already exists when it does not is material.

**Calibration examples** (invented cases outside the test set):

- **0.** A billing answer says only “done”. A CI answer says only “all clear” when the logs contain failed checks. Both receive (0, 0, 0).
- **2.** In a five-ticket batch, three customers are identified correctly, one of their balances belongs to someone else, and two are omitted. The reply to the misattributed balance requests another payment. Score (2, 2, 2) if the other recommendations are sound. In CI, one of two causes is fully diagnosed but the other is assigned the wrong error and its open issue is omitted. Rebuilding only the correctly diagnosed component helps part of the assignment. Score (2, 2, 2).
- **4.** For a billing reconciliation, the answer links each invoice to the right customer, explains the posted adjustment, computes the amount outstanding, distinguishes settled cases and gives each customer the correct next step. For CI, the answer accounts for every failed run, supplies the exact log evidence, explains the grouping and indicates which open issue has a corresponding change. Score (4, 4, 4) when all three conditions hold.
- Score dimensions independently. An answer that gives an accurate but incomplete list can retain a high correctness score while losing coverage. A missing or unusable answer receives three zeros. Score an intelligible answer even if it fails the JSON gate.

Submit an object keyed by opaque packet ID. Each value has exactly three fields: `correctness`, `completeness`, `action`. Each is an integer from 0 through 4. Read only the assigned pass directory, in filename order. Record suspected unblinding separately before unlocking any mapping. In adjudication score the answer afresh without reading earlier ratings.
