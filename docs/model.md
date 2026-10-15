# Model notes: definitions, interpretations, simplifications

Times are minutes; a week is 10 080 min, a shift 720 min. Parameters are in `data/minifab.json`
(sources in `docs/sources.md`).

## 1. Static capacity model (`src/fabcap/capacity.py`)

* **Machine time per job** = load + run + unload. This is the convention of Kempf's own capacity
  table (S1, section 1.9); the machine is occupied while an operator loads and unloads it.
* **Load** of a tool group = sum over products and route steps of starts x time / batch size, with
  **full batches** (3 lots) on diffusion, as in S1 and S3 (S2 allows partial batches; see
  `docs/sources.md`). The DES also runs full batches only, so at low load lots wait for their batch
  to fill - a consequence of this rule, not of the fab as such.
* **Expected litho setup per job** assumes consecutive jobs are independent draws from the visit
  mix ("unmanaged sequence"). With Kempf's mix this is 6.76 min per job. The DES shows that a real
  FIFO sequence is far less random than that (section 5 of the README).
* **Availability** = calendar - PM - EM, per tool. EM has two readings of the specification:
  `spec` = 2 events per machine-week x 7 h (the count S1's own arithmetic targets and its capacity
  table uses) and `renewal` = mean repair / (mean time to failure + mean repair) = 420 / 3420 of the
  calendar (2.95 events per machine-week, the distribution read literally as calendar time after
  each repair). `renewal` is the default.
* **Failure clock** (`em_minutes_per_tool`, `des_tbf_scale`): with a *calendar* clock the failure count
  per tool is fixed; with a *run-time* clock (S1: "a machine must be running") failures are proportional
  to run minutes, calibrated so that at Kempf's 84 starts with two implanters (3360 run min per
  implanter-week) the count equals the chosen reading. A third implanter then adds PM but no repairs.
  The four combinations are named `calendar_dist`, `calendar_spec`, `run_dist`, `run_spec`.
* **Utilisation** = load / available. **Bottleneck** = highest utilisation. **Max starts** = the total
  weekly starts, at the current mix, that puts the bottleneck exactly at the utilisation cap (load is
  linear in starts at fixed mix).
* **Personnel load** (`personnel_load`): operators do setup + load + unload, the technician does PM
  and repairs (under any of the four failure models); travel is ignored. With `spec` EM and no setups
  this reproduces S1's 840/1080 and 465/600 min per shift (77.8 % and 77.5 %). It pools people and
  ignores who may serve which tool, and it is an average: the DES shows that machines lose far more
  time waiting for people than these averages suggest.

## 2. Queueing approximation (`src/fabcap/queueing.py`)

* Queue time per group: the basic G/G/m formula, S6 eq. (1). S6 itself (citing Whitt 1993) reports
  that this form loses accuracy at low utilisation and switches to Whitt's improved form, which is
  not implemented here; low-load queue times from this model are therefore the least reliable part.
* Job-time mean/variance: mixture over the steps the group runs (each step deterministic) plus an
  independent setup component (litho), then PM and EM applied consecutively as non-preemptive
  outages with S7 eq. (2), with Ns = jobs per tool between outages. This is an approximation in two
  ways: EM really interrupts a run and scraps the lot (pre-emptive, and the lot's work is lost), and
  PM in the DES waits for a technician and floats within its window rather than following every Ns
  jobs. The open sources we could verify give only the non-preemptive formula.
* Arrival SCV ca2 is an input (default 1 = Poisson-like). For batch tools the batch arrival SCV is
  ca2/k and each lot additionally waits (k - 1)/(2 lambda_family) for its batch to fill. Batch
  families: S1 (any products), S5 per commercial product; test lots join S5 families in proportion.
  The ca2/k step assumes one renewal family; the diffusion queue merges several families, whose
  superposed batch stream is not renewal, so this is a further approximation.
* The queueing "u" is the non-idle fraction of calendar time (processing + setup + PM + repair);
  it reaches 1 at exactly the same load as the static utilisation (tested).
* Lot cycle time = sum over route visits of (queue + batch wait + step time + the group's mean
  setup/outage time per job). X-factor = cycle time / raw process time (845 min for Pa/Pb/TW).

## 3. Capital-planning MILP (`src/fabcap/planning.py`)

* Decisions: integer tools ordered per group and quarter, installed one quarter later (lead time);
  integer operators and technicians hired per quarter, available in the same quarter (illustrative
  hiring lead time 0), never laid off.
* Tool constraint per group and quarter: load x (1 - shortfall/demand) <= cap x available x tools, with
  load from the static model at that quarter's mix (incl. expected setups) and cap = 85 %.
* Operator constraint per quarter: (setup + load + unload minutes) x served fraction <= 85 % x 7560 min
  x operators (pooled: who may serve which tool is ignored here).
* Technician constraint per quarter: sum over groups of installed tools x (PM + expected repair minutes
  per tool) <= 85 % x 8400 min x technicians. This couples the groups: each tool bought adds technician
  work.
* Objective: discounted capex (2 %/quarter) + discounted labour cost of the extra heads (0.05 M$ per
  operator-quarter, 0.06 M$ per technician-quarter, illustrative) + 1 M$ per unserved lot/week per week.
* Orders that would arrive after Q8 are fixed to zero (they could only add cost).
* `src/fabcap/validate.py` re-derives machine, operator and technician loads from the JSON files with
  separate code, checks every constraint, integrality, lead time and the capex sum, and - when no
  shortfall - compares the MILP cost with a greedy plan that is optimal for that case: tools separate
  by group (install the running-maximum need, ordered as late as possible); those tool counts are
  pointwise minimal, so the technician need is pointwise minimal too, and hiring the running-maximum
  need of each role minimises labour cost. The MILP still equals the greedy plan in all three scenarios
  (`plan_*.json#validation.greedy_equals_milp`); the greedy plan is kept as a sanity bound.
* The plan's tool and staff counts for Q4 and Q8 are re-run in the DES (`results/plan_vs_des.json`);
  hired operators are assumed qualified on all three groups.

## 4. Discrete-event simulation (`src/fabcap/des.py`)

Modelled from S1: machines and their run/load/unload times; full-batch diffusion with S1/S5 mixing
rules and at most one test lot per batch; test lots never twice on the same machine of a multi-tool
group; sequence-dependent litho setups (an operator and the machine are both needed); PM per
shift/day; EM on implanters scrapping the lot in process; two operators with the eligibility of S1;
one technician; one transporter with S1's travel times.

Interpretations and simplifications (not fixed by the specification):

* **Failure clock** (`SimConfig.failure_clock`, `tbf_scale`). `calendar` (default): the time to the
  next failure is drawn U(24 h, 76 h) x `tbf_scale` of calendar time from the end of the previous
  repair; if it expires while the machine is not running, the failure happens at the start of the
  machine's next run (S1: "a machine must be running for the surprise breakdown to happen"), so an
  idle implanter still fails about as often as a busy one. `run`: the same distribution x
  `tbf_scale` is measured in run minutes, so only running ages the machine. `tbf_scale` is derived
  by `capacity.des_tbf_scale` for each named failure model: calendar 1.0 (2.95/week) or 1.54
  (2/week); run 0.38 or 0.56 (2.95 or 2 per week at 84 starts). Repair U(6 h, 8 h) needs a technician.
* **PM**: due at the window opening (start of shift/day, or 6/12 h after the previous PM, whichever
  is later). It is started between jobs, never interrupting one: when the machine finishes a job
  (or is idle) with PM due and a technician is free, it does the PM before taking the next job. If
  all technicians are busy, the machine keeps processing jobs until the window closes; after that it
  starts no new job and waits for a technician.
* **Personnel**: requests are served first-come-first-served (option: one group's requests first);
  1 min travel between adjacent cells; three off-time blocks per shift at fixed offsets
  (PO1 180/360/540 min, PO2 240/420/600 min, MT1 200/400/620 min into the shift), taken
  when the current task ends (no pre-emption). Extra people (what-if and plan check only): PO3
  300/480/660, PO4 210/390/570, MT2 260/460/680. With several technicians a request goes to whichever
  becomes free first.
* **Setup and load are one operator task**: the operator who sets the litho tool up also loads it,
  without releasing in between (S1: a setup "can only be done immediately prior to execution of the
  run"). Unload is a separate request after the run.
* **Transport**: one vehicle, FIFO requests, empty travel to the pick-up cell included.
* **Stockers**: capacity limits (18/12/12) are **not** enforced; the peak stocker content (lots in
  the cell that are not on a machine: waiting to be processed or waiting for the transporter) inside
  the measurement window is reported instead. Peaks above the limits mean the real line would block
  earlier. Under CONWIP release the peaks are meaningless for this purpose (40 lots are kept in the line).
* **Failure clock and idle tools**: with the calendar clock a lightly used implanter still fails about
  as often as a busy one and scraps the first lot it runs after the clock expires. This drives a large
  part of the "buy an implanter" what-if, which the README therefore reports as a range over the four
  failure models.
* **Release**: `deterministic` = each product at a constant interval with a random phase;
  `poisson`; `conwip` = keep W lots in the line (W = 40), mix by smooth weighted round robin - used
  to measure capacity (saturated output).
* **Dispatching**: FIFO by arrival at the group; CR = (due - now) / remaining raw process time with
  due = release + 3 x raw process time; SA = smallest setup from the litho tool's current state,
  FIFO on ties (FIFO at the other groups). Batches are formed from the rule-ordered queue: the
  first lot that can head a full compatible batch is taken with the next compatible lots.
* **Statistics**: 20 weeks warm-up, 100 weeks measured, 10 replications (seeds 101-110, the same
  seeds for every configuration = common random numbers), 95 % Student-t intervals. A run is flagged
  unstable if mean WIP in the last third of the window exceeds 1.5x the first third (and by > 10
  lots); cycle times of unstable runs are meaningless and are not reported as such. Output of an
  unstable open-release run (WIP still growing) is only an average over weeks 20-120 of that run, not a
  capacity and not a stationary quantity, so no confidence interval is attached to it (the README gives
  the range over the 10 runs instead).
* **Determinism**: Python's `random.Random` per stream; sampled durations are rounded to 1e-6 min
  so that platform `libm` differences cannot change the event order; results are written rounded to
  10 significant digits with LF line endings.

## 5. Agent (`src/fabcap/agent/`)

* Five tools wrap the models above (`tools.py`). Their outputs are already rounded to presentation
  precision and echo their inputs, so an answer can quote numbers verbatim.
* `validator.py`: a narrow list of names (`S1`-`S6`, `S3X`/`S4X`, `PO1`-`PO4`, `MT1`/`MT2`, `Ma`-`Me`,
  and exactly `G/G/1`, `G/G/m`, `M/M/1`) is removed, then every digit sequence counts as a number (also
  `x12`, `$18.5M`, `.5`). What is checked: each number must equal (as an absolute value, no rounding
  tolerance) a computed value of a non-error tool output from the same conversation; numbers inside a
  tool's `error` message never count; values a tool only echoes from its arguments
  (`demand_change_pct`, `from_quarter`, `extra_tools`) and outputs that are a direct transform of the
  arguments (each tool lists them in `arg_derived`: starts/week after a demand change, a tool count
  after adding tools) count only if the user's question contains the same number; quarter names
  (`Q7`) must occur in a tool output or the question; dotted digit runs such as `81.5.999` are
  rejected. What is NOT guaranteed: numbers spelled out as words or in other notations (`1e2`), a
  correct number attached to the wrong claim or with the wrong sign/direction, misuse of small integers
  that occur in many outputs (ranks, tool counts), and a wrong tool argument whose number the user did
  state. So "passes the validator" means "every number is traceable to a tool output or the question",
  not "the answer is right".
* `llm.py`: tool-calling loop against a local Ollama model, temperature 0. A rejected answer gets
  one repair turn; if it is still rejected the answer is replaced by the deterministic template of
  the same tool outputs, and this replacement is counted. An answer with numbers but no tool call is
  replaced by a refusal.
* `router.py`: the no-LLM baseline - regular expressions pick one tool and its arguments, templates
  write the answer. Rebuilt on 2026-10-02 from the 24 dev questions only (plus the fab's own names);
  out-of-scope questions are refused by principles (no domain word; a named entity outside the model;
  "my ..." or a non-fab unit per time; a writing task), not by a list of companies or topics.
* Evaluation (`evaluate.py`, `questions.py`): dev 24 (20 in scope), test 24 (20; reworded dev
  siblings), hard 12 (10; written after the first systems), and a fresh held-out set of 36 (24 in
  scope, 12 out of scope; `data/agent_heldout.json`, SHA-256 pinned in `questions.py`) written after
  the rebuild. Only the held-out set is still unseen; test and hard were seen before the rebuild. The
  expected numbers are produced by running the gold tool call. An in-scope
  answer counts as correct only if it passes the validator and contains every expected number with
  the same value and every expected keyword (snake_case option ids may be paraphrased: each token's
  first four letters must start a word of the answer). An out-of-scope question counts as correct
  only if refused.
