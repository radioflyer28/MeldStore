# Full interval comparison tables

Median of the three round medians, in milliseconds. Each round has 40 measured reads per query.
Q0/Q1 are narrow selections; Q2 is half-span; Q3 is full-span. No cross-adapter ranking is implied.

| Adapter | Statistics | Layout | Q0 ms | Q1 ms | Q2 ms | Q3 ms |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| sqlite | fresh | start | 0.072 | 0.436 | 837.039 | 1697.040 |
| sqlite | fresh | start-plus-end | 47.615 | 50.448 | 1794.730 | 3306.388 |
| sqlite | fresh | covering-start | 0.115 | 0.783 | 141.891 | 258.723 |
| sqlite | fresh | covering-end | 55.031 | 52.871 | 149.954 | 237.113 |
| sqlite | analyzed | start | 0.165 | 0.734 | 1750.477 | 3250.402 |
| sqlite | analyzed | start-plus-end | 52.845 | 57.109 | 1610.793 | 3288.119 |
| sqlite | analyzed | covering-start | 0.139 | 0.530 | 124.204 | 250.903 |
| sqlite | analyzed | covering-end | 61.146 | 59.489 | 145.836 | 280.562 |
| melddb | fresh | start | 0.106 | 0.870 | 1275.627 | 2625.506 |
| melddb | fresh | start-plus-end | 58.139 | 49.788 | 1711.903 | 3280.506 |
| melddb | fresh | covering-start | 0.178 | 0.572 | 120.313 | 259.705 |
| melddb | fresh | covering-end | 52.450 | 53.776 | 148.689 | 237.275 |
| melddb | analyzed | start | 0.106 | 0.611 | 934.637 | 1549.373 |
| melddb | analyzed | start-plus-end | 24.399 | 54.055 | 1083.098 | 1554.013 |
| melddb | analyzed | covering-start | 0.213 | 0.602 | 150.852 | 282.798 |
| melddb | analyzed | covering-end | 52.840 | 51.314 | 145.252 | 247.745 |
| postgres | fresh | start | 3.155 | 3.195 | 42.417 | 80.148 |
| postgres | fresh | start-plus-end | 5.612 | 6.263 | 43.017 | 82.441 |
| postgres | fresh | covering-start | 2.850 | 3.293 | 45.075 | 84.108 |
| postgres | fresh | covering-end | 7.329 | 8.086 | 49.373 | 87.800 |
| postgres | analyzed | start | 2.900 | 3.232 | 46.330 | 76.937 |
| postgres | analyzed | start-plus-end | 5.459 | 5.898 | 47.455 | 80.099 |
| postgres | analyzed | covering-start | 2.860 | 3.647 | 47.748 | 70.091 |
| postgres | analyzed | covering-end | 7.652 | 14.108 | 49.084 | 86.711 |

## Costs

Build and rollback columns are medians across the three independently prepared rounds.
DB allocation is MiB; SQLite excludes WAL and cannot separate index allocation. Rollback is not durable commit.

| Adapter | Statistics | Layout | Build ms | DB MiB | Rollback 100 ms | Rollback 1000 ms | Rollback 10000 ms |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| sqlite | fresh | start | 331.26 | 47.52 | 29.44 | 30.71 | 28.35 |
| sqlite | fresh | start-plus-end | 1185.83 | 66.06 | 90.60 | 84.95 | 85.61 |
| sqlite | fresh | covering-start | 849.55 | 52.93 | 71.12 | 67.91 | 70.30 |
| sqlite | fresh | covering-end | 931.15 | 52.93 | 70.91 | 68.25 | 66.72 |
| sqlite | analyzed | start | 678.28 | 47.52 | 65.44 | 75.49 | 65.91 |
| sqlite | analyzed | start-plus-end | 1379.20 | 66.07 | 86.01 | 87.63 | 85.11 |
| sqlite | analyzed | covering-start | 975.56 | 52.94 | 69.99 | 79.44 | 69.33 |
| sqlite | analyzed | covering-end | 935.94 | 52.94 | 77.59 | 87.87 | 63.81 |
| melddb | fresh | start | 330.27 | 47.52 | 56.61 | 48.90 | 28.54 |
| melddb | fresh | start-plus-end | 1348.62 | 66.06 | 85.81 | 85.61 | 85.63 |
| melddb | fresh | covering-start | 973.13 | 52.93 | 88.55 | 84.92 | 78.52 |
| melddb | fresh | covering-end | 854.80 | 52.93 | 77.34 | 67.64 | 72.84 |
| melddb | analyzed | start | 302.81 | 47.52 | 37.79 | 38.14 | 37.49 |
| melddb | analyzed | start-plus-end | 1358.81 | 66.07 | 84.25 | 105.00 | 99.97 |
| melddb | analyzed | covering-start | 969.16 | 52.94 | 72.59 | 70.82 | 80.52 |
| melddb | analyzed | covering-end | 992.16 | 52.94 | 96.12 | 77.35 | 85.30 |
| postgres | fresh | start | 451.15 | 50.61 | 1215.02 | 995.44 | 1026.49 |
| postgres | fresh | start-plus-end | 973.11 | 53.01 | 1367.28 | 1019.45 | 1095.91 |
| postgres | fresh | covering-start | 578.21 | 51.40 | 1125.43 | 1005.94 | 993.34 |
| postgres | fresh | covering-end | 573.45 | 51.40 | 1070.60 | 1006.93 | 912.64 |
| postgres | analyzed | start | 442.94 | 50.68 | 1228.97 | 989.88 | 1031.38 |
| postgres | analyzed | start-plus-end | 926.42 | 53.07 | 1215.03 | 1056.61 | 938.86 |
| postgres | analyzed | covering-start | 552.40 | 51.46 | 1216.94 | 964.93 | 988.63 |
| postgres | analyzed | covering-end | 555.86 | 51.46 | 1258.39 | 1058.92 | 1034.01 |

## All round read ratios

Relative to start-only in the same adapter/statistics/round; below 1 is faster.
Each triple is rounds 0, 1, 2. Session boundaries remain a comparison limitation.

| Adapter | Statistics | Layout | Q0 ratios | Q1 ratios | Q2 ratios | Q3 ratios |
| --- | --- | --- | --- | --- | --- | --- |
| sqlite | fresh | start-plus-end | 792.512, 661.324, 466.829 | 70.678, 116.374, 66.330 | 2.144, 2.064, 2.095 | 1.967, 2.268, 1.739 |
| sqlite | fresh | covering-start | 1.886, 1.592, 1.647 | 0.932, 1.848, 1.036 | 0.157, 0.196, 0.166 | 0.150, 0.194, 0.145 |
| sqlite | fresh | covering-end | 630.571, 764.313, 858.276 | 62.964, 116.914, 129.597 | 0.179, 0.187, 0.182 | 0.143, 0.162, 0.133 |
| sqlite | analyzed | start-plus-end | 234.603, 334.655, 321.245 | 90.315, 87.766, 47.529 | 0.996, 0.976, 0.857 | 1.084, 1.081, 0.868 |
| sqlite | analyzed | covering-start | 0.531, 0.889, 0.847 | 0.690, 0.814, 0.489 | 0.078, 0.068, 0.070 | 0.077, 0.081, 0.066 |
| sqlite | analyzed | covering-end | 279.844, 560.640, 279.704 | 81.058, 142.061, 43.316 | 0.092, 0.142, 0.064 | 0.086, 0.136, 0.063 |
| melddb | fresh | start-plus-end | 167.500, 492.659, 817.288 | 29.389, 94.695, 73.352 | 0.564, 2.376, 1.623 | 0.545, 2.231, 1.504 |
| melddb | fresh | covering-start | 0.469, 2.774, 2.211 | 0.337, 1.032, 0.704 | 0.040, 0.213, 0.094 | 0.044, 0.203, 0.095 |
| melddb | fresh | covering-end | 151.108, 463.136, 656.132 | 29.796, 143.118, 61.811 | 0.050, 0.207, 0.113 | 0.040, 0.174, 0.090 |
| melddb | analyzed | start-plus-end | 156.556, 230.613, 307.878 | 34.485, 43.851, 183.949 | 0.545, 1.159, 1.186 | 0.598, 1.003, 1.003 |
| melddb | analyzed | covering-start | 0.693, 2.018, 2.353 | 0.337, 1.012, 1.388 | 0.053, 0.161, 0.180 | 0.049, 0.201, 0.174 |
| melddb | analyzed | covering-end | 200.989, 499.429, 306.464 | 39.526, 83.983, 116.742 | 0.052, 0.155, 0.102 | 0.052, 0.160, 0.082 |
| postgres | fresh | start-plus-end | 1.850, 1.823, 1.779 | 1.824, 1.674, 1.963 | 0.973, 1.016, 1.084 | 0.951, 1.056, 1.050 |
| postgres | fresh | covering-start | 0.931, 1.017, 0.903 | 0.959, 0.973, 1.056 | 0.970, 1.070, 1.095 | 0.924, 1.061, 1.072 |
| postgres | fresh | covering-end | 2.342, 2.671, 2.323 | 2.356, 1.989, 2.548 | 1.145, 1.046, 1.164 | 1.088, 1.029, 1.119 |
| postgres | analyzed | start-plus-end | 2.521, 1.882, 1.865 | 2.188, 2.034, 1.505 | 1.909, 0.989, 1.123 | 1.812, 0.899, 1.041 |
| postgres | analyzed | covering-start | 0.869, 1.375, 1.153 | 0.970, 1.286, 1.014 | 0.969, 1.011, 1.203 | 0.957, 0.808, 1.024 |
| postgres | analyzed | covering-end | 2.325, 4.769, 3.084 | 1.754, 4.866, 8.692 | 1.059, 1.890, 1.113 | 1.244, 1.907, 1.127 |
