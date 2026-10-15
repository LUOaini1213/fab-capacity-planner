# Sources

All URLs below were opened and read on **2026-10-02** (retrieval date). "Verified" lists what was
checked against the source text, not just that the page exists.

## Reference fab: Intel Five-Machine Six-Step Mini-Fab

| Id | Source | Verified |
|----|--------|----------|
| S1 | K. Kempf, *Intel Five-Machine Six Step Mini-Fab Description*, Intel/ASU report, 1994 (as cited in S3). Original URL `aar.faculty.asu.edu/research/intel/papers/fabspec.html` no longer resolves; archived copy: <http://web.archive.org/web/20210227001255/http://aar.faculty.asu.edu/research/intel/papers/fabspec.html> | Starts 51 Pa + 30 Pb + 3 TW lots/week; route S1-S6 for all products; run times S1 225, S2 30, S3 55, S4 50, S5 255, S6 10 min; load/unload 20/40 (Ma, Mb), 15/15 (Mc, Md), 10/10 (Me); batches of 3 on Ma/Mb with the S1 mixing and S5 no-mixing rules and at most one test lot; test lot never twice on the same machine; Me setups 10 (step change), 5 (product/test change), 12 (both); PM 75 min/day (Ma, Mb), 120 min/shift (Mc, Md), 30 min/shift (Me); PM window rule (shift start or 6 h after last PM; day start or 12 h); EM on Mc/Md: repair 420 +/- 60 min, every 50 +/- 26 h, lot in process lost ("four lots will be lost each week"); two operators (PO1: Ma/Mb/Mc/Md, PO2: Mc/Md/Me), 540 of 720 min available per shift; one technician, 600 of 720 min; layout S-C1-C2-C3-O, 4 min per adjacent move, 1 min load/unload, one lot in transport at a time; stocker limits 18/12/12. Section 1.9 capacity table: 1200/1365 = 87.9 %, 840/1080 = 77.8 %, 630/690 = 91.3 %; operators 77.8 %; technician 465 of 600 min/shift (77.5 %), computed with **2 EM per machine-week**. |
| S2 | L. F. McGinnis, *Model-Based Systems Engineering and the Intel MiniFab Case*, rev. 28 Apr 2023. <https://leonmcginnis.com/wp-content/uploads/2023/04/IntelMiniFabCaseStudy02012021.pdf> | Same products, times, batching, setups, PM, EM (failure 24-76 h after the previous repair, repair 6-8 h), personnel and layout as S1; attributes the case to Karl Kempf (Intel, mid-1990s) and links to S1. |
| S3 | J. P. A. van den Berk, *Analysis of the Intel Five-Machine Six Step Mini-Fab*, MSc thesis, Eindhoven University of Technology, 2004 (SE 420383). <https://dc.wtb.tue.nl/lefeber/do_download_pdf.php?id=74> | Same products, routes, times, batching, setups and personnel as S1, but a **different failure model**: failures happen only while an implanter is processing, and a model parameter is tuned per release rate until there are 400 downs per 100 weeks for the two implanters, i.e. **2 per machine-week** (App. B, Table F.1). Results used for comparison only: with FIFO control a throughput of 47.4 products/week at the required 84 starts and a maximum release rate of 57.5 products/week before WIP grows (ch. 3.6, App. D, App. F); lithography is the bottleneck. S3 defines throughput as products that reach the finish area **plus products lost to unscheduled downs** (App. D), so its 47.4 is not a count of good lots. |

**Discrepancies between the sources.** (1) Batch size: S1 says the diffusion tool "can run if three
appropriate lots are available to load at the same time" (and lists batch sizes of 2 or 3 as an
"obvious extension"); S3 says "a batch contains three products"; S2 says the tools "can accept batches
with up to three wafer lots". This repository follows S1/S3 (**full batches only**); this choice drives
the low-load diffusion waiting in the X-factor results. (2) Failure frequency and clock: S1 section 1.7
says EM happens "randomly once each half week", that "a machine must be running for the surprise
breakdown to happen", and derives the interval 50 +/- 26 h so that the EM window plus a worst-case repair
stays within half a week; its capacity table uses 2 per machine-week. So the specification's arithmetic
targets **2 failures per machine-week**. Read literally as "next failure 50 +/- 26 h of calendar time
after the previous repair", the same numbers give 168 / 57 = 2.95 per week. S3 follows the 2-per-week
reading with a running-only clock. This repository carries both readings and both clocks (calendar time
or run time) as options; the default DES uses the literal calendar reading (2.95), and the README reports
the results that depend on it as ranges over the four combinations.

Year of S1: S3's bibliography cites it as "K. Kempf. Intel Five-Machine Six Step Mini-Fab Description.
Intel/ASU Report, 1994."; this repository therefore cites it as an Intel/ASU report, 1994 (as cited in
S3). Other secondary sources differ: a 2023 thesis paper on arXiv (2311.04947, ref. [22], checked
2026-10-02) dates the same description 1995, and S2 says "mid-1990's".

All Mini-Fab parameters in `data/minifab.json` come from S1; S2 and S3 agree with them except for the
batch-size reading (S2) and the failure model (S3) described above. The following are **not** in the
specification and are this repository's assumptions (see `docs/model.md`): off-time positions inside a
shift, operator/technician dispatching, which failure clock applies, and the release pattern.

## Formulas

| Id | Source | Used for | Verified |
|----|--------|----------|----------|
| S4 | J. F. C. Kingman, "The single server queue in heavy traffic", *Math. Proc. Cambridge Philos. Soc.* 57(4):902-904, 1961. doi:10.1017/S0305004100036094 (<https://doi.org/10.1017/S0305004100036094>) | G/G/1 queue time (m = 1 case) | Bibliographic data via Crossref API; the formula as stated in S6 and on <https://en.wikipedia.org/wiki/Kingman%27s_formula> |
| S5 | H. Sakasegawa, "An approximation formula Lq ~ alpha rho^beta / (1 - rho)", *Ann. Inst. Stat. Math.* 29:67-75, 1977. doi:10.1007/BF02532775 | origin of the u^(sqrt(2(m+1)) - 1) term | Bibliographic data via Crossref API (paper itself paywalled, not read) |
| S6 | R. E. A. Schelasin, "Estimating wafer processing cycle time using an improved G/G/m queue", *Proc. 2013 Winter Simulation Conference*. <https://informs-sim.org/wsc13papers/includes/files/334.pdf> | G/G/m queue time CTq = ((ca2 + ce2)/2) (u^(sqrt(2(m+1)) - 1) / (m(1 - u))) te, eq. (1), the basic form "as found in Hopp and Spearman" | Equation rendered from the PDF and read; the radical covers 2(m+1) only, so m = 1 gives Kingman. S6 section 3 states (citing Whitt 1993) that this basic form performs well at high utilisation but loses accuracy at lower utilisation, and replaces it with **Whitt's improved form** (its eqs. 2-9). This repository implements only the basic eq. (1) |
| S7 | L. F. P. Etman, C. P. L. Veeger, E. Lefeber, I. J. B. F. Adan, J. E. Rooda, "Aggregate modeling of semiconductor equipment using effective process times", *Proc. 2011 Winter Simulation Conference*. <https://www.win.tue.nl/~iadan/1ck110/EPT-wintersim-2011.pdf> | effective process time for non-preemptive outages, eq. (2): te = t0 + ts/Ns, ce2 = (s0^2 + ss^2/Ns + (Ns - 1)/Ns^2 ts^2)/te^2; definition u = te/(m ta) | Read from the PDF. Its typeset eq. (1) draws the radical over "2(m+1) - 1"; we follow S6 because only that form reduces to Kingman at m = 1 |
| S8 | W. Whitt, "Approximations for the GI/G/m queue", *Production and Operations Management* 2(2):114-161, 1993. doi:10.1111/j.1937-5956.1993.tb00094.x | not implemented; cited because S6 takes its improved (low-utilisation) G/G/m form from Whitt, and S7 names "Sakasegawa's and Whitt's" closed form for the basic approximation | Bibliographic data via Crossref API only; the paper itself was not read |
| S9 | J. D. C. Little, "A proof for the queuing formula L = lambda W", *Operations Research* 9(3):383-387, 1961. doi:10.1287/opre.9.3.383 | sanity test WIP vs throughput x cycle time | Bibliographic data via Crossref API |
| S10 | SEMI E10-0422, *Specification for Definition and Measurement of Equipment Reliability, Availability, and Maintainability (RAM) and Utilization*. <https://store-us.semi.org/products/e01000-semi-e10-specification-for-definition-and-measurement-of-equipment-reliability-availability-and-maintainability-ram-and-utilization> | vocabulary only (equipment states; "unscheduled downtime"; total vs operational utilisation) | Product page read; the standard itself is paywalled and was **not** read, so this repository does not claim E10-conformant metrics. Its own definitions are in `src/fabcap/capacity.py` |

Not used, because no open copy could be verified: the preemptive-outage formula for te/ce2 in Hopp &
Spearman's *Factory Physics* (Table 8.2). EM is instead treated as a non-preemptive outage with S7
eq. (2) - an approximation, flagged in `docs/model.md`.

Derived here without an external source (elementary): mean batch-formation wait (k - 1)/(2 lambda) for
full batches of k from a family arriving at rate lambda (linearity of expectation), and batch
inter-arrival SCV ca2/k for k i.i.d. inter-arrivals. The ca2/k step treats the batch stream as if it came
from one renewal family; at diffusion several families (S1 mixed, S5 per product, test lots) are merged
into one queue, and the superposition of batch streams is not a renewal process with SCV ca2/k, so this
is a further approximation.

## Software

| Package | Docs (retrieved 2026-10-02) |
|---------|------|
| SimPy 4.1.1 | <https://simpy.readthedocs.io/en/4.1.1/> |
| PuLP 2.9.0 (bundled CBC) | <https://coin-or.github.io/pulp/> |
| Ollama tool calling (server 0.35.0 used locally) | <https://docs.ollama.com/capabilities/tool-calling> |

## Illustrative (invented) data

Everything in `data/planning.json` - tool prices, the 1-quarter lead time, the 85 % utilisation cap,
the 2 %/quarter discount rate, the 8-quarter demand forecast, the extra product **Pc** and its two
extra steps, and the what-if costs - is made up for demonstration. None of it comes from a source.
