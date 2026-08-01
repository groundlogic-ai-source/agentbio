# Benchmark case-selection attrition table

Seed: 20260731 · criteria: `benchmark_case_selection_criteria.md` · full run

| Stage | Cases remaining |
| --- | --- |
| 0. dataset rows | 9057 |
| I1. status=Approved & label=repurposed-success | 5582 |
| E2. combination products excluded ('+' in name) | 5582 |
| E1/I2. Small molecule per enrichment | 4866 |
| E1/I2. EMPTY molecule_type excluded (disclosed; ChEMBL-era failures) | -456 |
| E3. development-suite drugs excluded (5 drugs) | 4852 |
| I3a. benchmark indication in Orphanet universe | 248 |
| E4. one case per drug (117 unique drugs) | 117 |
| I3b. EFO resolved (v3 pipeline) | 108 |
| I4. at least one target in discovery universe | 101 |

**Primary cases selected:** 50 (seed 20260731, stratum cap 0.4)
**Coverage failures (disclosed, not discovery failures):** 7
