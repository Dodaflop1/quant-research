# Sensitivity of the diffusion fits to preprocessing

15 of 15 cells completed. Rows are `--merge-window`, columns are `--period`. Every other flag is at `fit_diffusion.py`'s default.

Branching-ratio cells show the median across markets with the 10th-90th percentile band beneath.

### Branching ratio — naive

| merge \ period | 900s | 3600s | 86400s |
|---|---|---|---|
| **0.01s** | 0.466 <sub>[0.208, 0.867]</sub> | 0.466 <sub>[0.208, 0.867]</sub> | 0.466 <sub>[0.208, 0.867]</sub> |
| **0.03s** | 0.466 <sub>[0.207, 0.866]</sub> | 0.466 <sub>[0.207, 0.866]</sub> | 0.466 <sub>[0.207, 0.866]</sub> |
| **0.1s** | 0.723 <sub>[0.203, 0.868]</sub> | 0.723 <sub>[0.203, 0.868]</sub> | 0.723 <sub>[0.203, 0.868]</sub> |
| **0.3s** | 0.725 <sub>[0.198, 0.870]</sub> | 0.725 <sub>[0.198, 0.870]</sub> | 0.725 <sub>[0.198, 0.870]</sub> |
| **1s** | 0.726 <sub>[0.195, 0.868]</sub> | 0.726 <sub>[0.195, 0.868]</sub> | 0.726 <sub>[0.195, 0.868]</sub> |

### Branching ratio — deseasonalised

| merge \ period | 900s | 3600s | 86400s |
|---|---|---|---|
| **0.01s** | 0.467 <sub>[0.210, 0.867]</sub> | 0.467 <sub>[0.213, 0.868]</sub> | 0.321 <sub>[0.162, 0.894]</sub> |
| **0.03s** | 0.466 <sub>[0.208, 0.866]</sub> | 0.467 <sub>[0.211, 0.866]</sub> | 0.321 <sub>[0.162, 0.893]</sub> |
| **0.1s** | 0.723 <sub>[0.204, 0.868]</sub> | 0.722 <sub>[0.207, 0.868]</sub> | 0.496 <sub>[0.164, 0.893]</sub> |
| **0.3s** | 0.725 <sub>[0.199, 0.870]</sub> | 0.725 <sub>[0.203, 0.870]</sub> | 0.524 <sub>[0.192, 0.895]</sub> |
| **1s** | 0.726 <sub>[0.196, 0.868]</sub> | 0.726 <sub>[0.200, 0.868]</sub> | 0.529 <sub>[0.188, 0.893]</sub> |

### Median per-market shrink (naive − deseasonalised)

| merge \ period | 900s | 3600s | 86400s |
|---|---|---|---|
| **0.01s** | -0.0000 | -0.0002 | +0.0040 |
| **0.03s** | -0.0000 | -0.0002 | +0.0042 |
| **0.1s** | -0.0000 | -0.0000 | +0.0052 |
| **0.3s** | -0.0000 | -0.0000 | +0.0057 |
| **1s** | -0.0000 | -0.0000 | +0.0078 |

### Windows passing both diagnostics

| merge \ period | 900s | 3600s | 86400s |
|---|---|---|---|
| **0.01s** | 5/18 | 5/18 | 5/18 |
| **0.03s** | 6/18 | 6/18 | 5/18 |
| **0.1s** | 7/18 | 7/18 | 6/18 |
| **0.3s** | 7/18 | 7/18 | 6/18 |
| **1s** | 7/18 | 7/18 | 6/18 |

### Markets beating held-out Poisson

| merge \ period | 900s | 3600s | 86400s |
|---|---|---|---|
| **0.01s** | 9/9 | 9/9 | 9/9 |
| **0.03s** | 9/9 | 9/9 | 9/9 |
| **0.1s** | 9/9 | 9/9 | 9/9 |
| **0.3s** | 9/9 | 9/9 | 9/9 |
| **1s** | 9/9 | 9/9 | 9/9 |

### Events surviving the merge

| merge \ period | 900s | 3600s | 86400s |
|---|---|---|---|
| **0.01s** | 69.5% | 69.5% | 69.5% |
| **0.03s** | 69.5% | 69.5% | 69.5% |
| **0.1s** | 69.2% | 69.2% | 69.2% |
| **0.3s** | 69.0% | 69.0% | 69.0% |
| **1s** | 68.7% | 68.7% | 68.7% |

## Verdict

Deseasonalised branching ratio ranges **0.321 to 0.726** across the grid — a spread of **0.405**. The real slow arm's bimodal split is 0.23 vs 0.88, a spread of 0.65.

A grid spread well below 0.65 means these two constants are not what produces the split, and the explanation lies elsewhere — heterogeneity across markets, or non-stationarity within windows. A grid spread approaching it means the published numbers are an artifact of the constants and must be quoted with this table.

Diagnostic pass rate ranges 28% to 39%.
