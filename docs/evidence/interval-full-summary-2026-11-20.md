# Interval metadata experiment

Recovery disclosure: session 2 ends at its last verified checkpoint, not at
process exit. Its subsequent continuity is unknown. Owned cleanup was verified
later by explicit recovery; see the report's recoveries audit and
[verification](../interval-query-verification.md#full-metadata-acceptance).

Profile: full. Scope: metadata only. Warm queries; OS cache uncontrolled.

Ratios compare p50 to start-only within the same adapter, statistics treatment and round.
Below 1 is faster. All rounds are shown; mixed/noisy results do not establish a winner.

| Adapter | Statistics | Layout | Query | Round p50 ratios | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | ---: | ---: |
| melddb | analyzed | covering-end | 0 | 200.989, 499.429, 306.464 | 52.323 | 79.864 |
| melddb | analyzed | covering-end | 1 | 39.526, 83.983, 116.742 | 54.438 | 72.183 |
| melddb | analyzed | covering-end | 2 | 0.052, 0.155, 0.102 | 139.309 | 199.944 |
| melddb | analyzed | covering-end | 3 | 0.052, 0.160, 0.082 | 246.333 | 342.885 |
| melddb | analyzed | covering-start | 0 | 0.693, 2.018, 2.353 | 0.216 | 0.295 |
| melddb | analyzed | covering-start | 1 | 0.337, 1.012, 1.388 | 0.601 | 0.934 |
| melddb | analyzed | covering-start | 2 | 0.053, 0.161, 0.180 | 139.452 | 191.221 |
| melddb | analyzed | covering-start | 3 | 0.049, 0.201, 0.174 | 280.643 | 339.639 |
| melddb | analyzed | start | 0 | 1.000, 1.000, 1.000 | 0.106 | 0.372 |
| melddb | analyzed | start | 1 | 1.000, 1.000, 1.000 | 0.636 | 1.643 |
| melddb | analyzed | start | 2 | 1.000, 1.000, 1.000 | 934.637 | 3092.360 |
| melddb | analyzed | start | 3 | 1.000, 1.000, 1.000 | 1557.491 | 7832.124 |
| melddb | analyzed | start-plus-end | 0 | 156.556, 230.613, 307.878 | 24.760 | 62.445 |
| melddb | analyzed | start-plus-end | 1 | 34.485, 43.851, 183.949 | 54.055 | 90.401 |
| melddb | analyzed | start-plus-end | 2 | 0.545, 1.159, 1.186 | 1454.281 | 2654.778 |
| melddb | analyzed | start-plus-end | 3 | 0.598, 1.003, 1.003 | 1582.810 | 3886.769 |
| melddb | fresh | covering-end | 0 | 151.108, 463.136, 656.132 | 52.070 | 62.449 |
| melddb | fresh | covering-end | 1 | 29.796, 143.118, 61.811 | 53.915 | 71.282 |
| melddb | fresh | covering-end | 2 | 0.050, 0.207, 0.113 | 146.598 | 185.016 |
| melddb | fresh | covering-end | 3 | 0.040, 0.174, 0.090 | 239.573 | 310.291 |
| melddb | fresh | covering-start | 0 | 0.469, 2.774, 2.211 | 0.174 | 0.427 |
| melddb | fresh | covering-start | 1 | 0.337, 1.032, 0.704 | 0.582 | 0.891 |
| melddb | fresh | covering-start | 2 | 0.040, 0.213, 0.094 | 127.707 | 182.501 |
| melddb | fresh | covering-start | 3 | 0.044, 0.203, 0.095 | 263.081 | 337.462 |
| melddb | fresh | start | 0 | 1.000, 1.000, 1.000 | 0.107 | 0.409 |
| melddb | fresh | start | 1 | 1.000, 1.000, 1.000 | 0.883 | 1.854 |
| melddb | fresh | start | 2 | 1.000, 1.000, 1.000 | 1275.627 | 3830.662 |
| melddb | fresh | start | 3 | 1.000, 1.000, 1.000 | 2625.506 | 6207.908 |
| melddb | fresh | start-plus-end | 0 | 167.500, 492.659, 817.288 | 57.408 | 78.686 |
| melddb | fresh | start-plus-end | 1 | 29.389, 94.695, 73.352 | 50.207 | 75.045 |
| melddb | fresh | start-plus-end | 2 | 0.564, 2.376, 1.623 | 1749.008 | 2200.282 |
| melddb | fresh | start-plus-end | 3 | 0.545, 2.231, 1.504 | 3293.123 | 4405.971 |
| postgres | analyzed | covering-end | 0 | 2.325, 4.769, 3.084 | 8.203 | 15.318 |
| postgres | analyzed | covering-end | 1 | 1.754, 4.866, 8.692 | 14.108 | 29.964 |
| postgres | analyzed | covering-end | 2 | 1.059, 1.890, 1.113 | 49.492 | 93.739 |
| postgres | analyzed | covering-end | 3 | 1.244, 1.907, 1.127 | 89.728 | 173.137 |
| postgres | analyzed | covering-start | 0 | 0.869, 1.375, 1.153 | 3.095 | 4.603 |
| postgres | analyzed | covering-start | 1 | 0.970, 1.286, 1.014 | 3.571 | 4.673 |
| postgres | analyzed | covering-start | 2 | 0.969, 1.011, 1.203 | 48.000 | 56.785 |
| postgres | analyzed | covering-start | 3 | 0.957, 0.808, 1.024 | 70.091 | 88.478 |
| postgres | analyzed | start | 0 | 1.000, 1.000, 1.000 | 2.944 | 4.130 |
| postgres | analyzed | start | 1 | 1.000, 1.000, 1.000 | 3.247 | 4.246 |
| postgres | analyzed | start | 2 | 1.000, 1.000, 1.000 | 45.593 | 51.244 |
| postgres | analyzed | start | 3 | 1.000, 1.000, 1.000 | 77.892 | 91.800 |
| postgres | analyzed | start-plus-end | 0 | 2.521, 1.882, 1.865 | 5.567 | 9.243 |
| postgres | analyzed | start-plus-end | 1 | 2.188, 2.034, 1.505 | 5.922 | 9.917 |
| postgres | analyzed | start-plus-end | 2 | 1.909, 0.989, 1.123 | 48.991 | 95.672 |
| postgres | analyzed | start-plus-end | 3 | 1.812, 0.899, 1.041 | 87.494 | 135.578 |
| postgres | fresh | covering-end | 0 | 2.342, 2.671, 2.323 | 7.352 | 9.085 |
| postgres | fresh | covering-end | 1 | 2.356, 1.989, 2.548 | 7.570 | 11.214 |
| postgres | fresh | covering-end | 2 | 1.145, 1.046, 1.164 | 48.416 | 59.942 |
| postgres | fresh | covering-end | 3 | 1.088, 1.029, 1.119 | 87.696 | 108.814 |
| postgres | fresh | covering-start | 0 | 0.931, 1.017, 0.903 | 2.887 | 3.797 |
| postgres | fresh | covering-start | 1 | 0.959, 0.973, 1.056 | 3.180 | 4.130 |
| postgres | fresh | covering-start | 2 | 0.970, 1.070, 1.095 | 43.955 | 48.800 |
| postgres | fresh | covering-start | 3 | 0.924, 1.061, 1.072 | 82.565 | 91.886 |
| postgres | fresh | start | 0 | 1.000, 1.000, 1.000 | 3.041 | 4.312 |
| postgres | fresh | start | 1 | 1.000, 1.000, 1.000 | 3.178 | 4.254 |
| postgres | fresh | start | 2 | 1.000, 1.000, 1.000 | 42.884 | 46.782 |
| postgres | fresh | start | 3 | 1.000, 1.000, 1.000 | 80.823 | 90.104 |
| postgres | fresh | start-plus-end | 0 | 1.850, 1.823, 1.779 | 5.541 | 7.266 |
| postgres | fresh | start-plus-end | 1 | 1.824, 1.674, 1.963 | 5.960 | 7.199 |
| postgres | fresh | start-plus-end | 2 | 0.973, 1.016, 1.084 | 43.480 | 48.117 |
| postgres | fresh | start-plus-end | 3 | 0.951, 1.056, 1.050 | 82.017 | 90.394 |
| sqlite | analyzed | covering-end | 0 | 279.844, 560.640, 279.704 | 61.757 | 99.115 |
| sqlite | analyzed | covering-end | 1 | 81.058, 142.061, 43.316 | 60.632 | 96.970 |
| sqlite | analyzed | covering-end | 2 | 0.092, 0.142, 0.064 | 150.742 | 257.180 |
| sqlite | analyzed | covering-end | 3 | 0.086, 0.136, 0.063 | 280.562 | 421.328 |
| sqlite | analyzed | covering-start | 0 | 0.531, 0.889, 0.847 | 0.137 | 0.184 |
| sqlite | analyzed | covering-start | 1 | 0.690, 0.814, 0.489 | 0.535 | 0.647 |
| sqlite | analyzed | covering-start | 2 | 0.078, 0.068, 0.070 | 124.401 | 153.009 |
| sqlite | analyzed | covering-start | 3 | 0.077, 0.081, 0.066 | 248.351 | 281.516 |
| sqlite | analyzed | start | 0 | 1.000, 1.000, 1.000 | 0.173 | 0.241 |
| sqlite | analyzed | start | 1 | 1.000, 1.000, 1.000 | 0.836 | 2.299 |
| sqlite | analyzed | start | 2 | 1.000, 1.000, 1.000 | 1749.081 | 2094.656 |
| sqlite | analyzed | start | 3 | 1.000, 1.000, 1.000 | 3333.440 | 4012.891 |
| sqlite | analyzed | start-plus-end | 0 | 234.603, 334.655, 321.245 | 52.944 | 68.093 |
| sqlite | analyzed | start-plus-end | 1 | 90.315, 87.766, 47.529 | 57.337 | 273.351 |
| sqlite | analyzed | start-plus-end | 2 | 0.996, 0.976, 0.857 | 1615.217 | 1898.119 |
| sqlite | analyzed | start-plus-end | 3 | 1.084, 1.081, 0.868 | 3332.581 | 5817.079 |
| sqlite | fresh | covering-end | 0 | 630.571, 764.313, 858.276 | 54.479 | 70.540 |
| sqlite | fresh | covering-end | 1 | 62.964, 116.914, 129.597 | 53.750 | 67.816 |
| sqlite | fresh | covering-end | 2 | 0.179, 0.187, 0.182 | 149.974 | 185.949 |
| sqlite | fresh | covering-end | 3 | 0.143, 0.162, 0.133 | 238.149 | 306.688 |
| sqlite | fresh | covering-start | 0 | 1.886, 1.592, 1.647 | 0.126 | 0.198 |
| sqlite | fresh | covering-start | 1 | 0.932, 1.848, 1.036 | 0.600 | 1.003 |
| sqlite | fresh | covering-start | 2 | 0.157, 0.196, 0.166 | 139.461 | 159.969 |
| sqlite | fresh | covering-start | 3 | 0.150, 0.194, 0.145 | 264.925 | 337.082 |
| sqlite | fresh | start | 0 | 1.000, 1.000, 1.000 | 0.074 | 0.116 |
| sqlite | fresh | start | 1 | 1.000, 1.000, 1.000 | 0.446 | 0.977 |
| sqlite | fresh | start | 2 | 1.000, 1.000, 1.000 | 807.803 | 1063.133 |
| sqlite | fresh | start | 3 | 1.000, 1.000, 1.000 | 1580.178 | 2301.870 |
| sqlite | fresh | start-plus-end | 0 | 792.512, 661.324, 466.829 | 47.159 | 76.034 |
| sqlite | fresh | start-plus-end | 1 | 70.678, 116.374, 66.330 | 49.473 | 75.998 |
| sqlite | fresh | start-plus-end | 2 | 2.144, 2.064, 2.095 | 1657.142 | 2097.664 |
| sqlite | fresh | start-plus-end | 3 | 1.967, 2.268, 1.739 | 3297.004 | 3777.627 |

## Costs per independently prepared round

Insertion cost includes deliberate rollback, not durable commit throughput.

| Adapter | Statistics | Layout | Round | Build ms | DB bytes | Insert+rollback p50 ms (100 / 1000 / 10000) |
| --- | --- | --- | ---: | ---: | ---: | --- |
| melddb | analyzed | covering-end | 0 | 1146.69 | 55508992 | 97.07 / 77.35 / 85.30 |
| melddb | analyzed | covering-end | 1 | 992.16 | 55508992 | 72.52 / 69.73 / 71.94 |
| melddb | analyzed | covering-end | 2 | 803.60 | 55508992 | 96.12 / 83.87 / 97.09 |
| melddb | analyzed | covering-start | 0 | 969.16 | 55508992 | 79.37 / 83.57 / 80.52 |
| melddb | analyzed | covering-start | 1 | 975.11 | 55508992 | 62.71 / 65.14 / 90.73 |
| melddb | analyzed | covering-start | 2 | 836.68 | 55508992 | 72.59 / 70.82 / 65.43 |
| melddb | analyzed | start | 0 | 1203.55 | 49831936 | 137.18 / 122.93 / 118.72 |
| melddb | analyzed | start | 1 | 302.81 | 49831936 | 37.79 / 38.14 / 37.49 |
| melddb | analyzed | start | 2 | 300.27 | 49831936 | 31.57 / 32.20 / 32.32 |
| melddb | analyzed | start-plus-end | 0 | 1358.81 | 69275648 | 84.25 / 105.00 / 75.13 |
| melddb | analyzed | start-plus-end | 1 | 576.30 | 69275648 | 88.38 / 84.72 / 108.45 |
| melddb | analyzed | start-plus-end | 2 | 1444.15 | 69275648 | 42.86 / 122.96 / 99.97 |
| melddb | fresh | covering-end | 0 | 664.37 | 55504896 | 77.34 / 67.64 / 72.84 |
| melddb | fresh | covering-end | 1 | 854.80 | 55504896 | 80.75 / 61.78 / 55.24 |
| melddb | fresh | covering-end | 2 | 930.48 | 55504896 | 66.36 / 82.43 / 77.96 |
| melddb | fresh | covering-start | 0 | 973.13 | 55504896 | 88.55 / 65.49 / 62.33 |
| melddb | fresh | covering-start | 1 | 1067.16 | 55504896 | 100.05 / 84.92 / 83.21 |
| melddb | fresh | covering-start | 2 | 830.52 | 55504896 | 78.61 / 85.24 / 78.52 |
| melddb | fresh | start | 0 | 1075.49 | 49827840 | 127.68 / 124.20 / 118.28 |
| melddb | fresh | start | 1 | 330.27 | 49827840 | 36.61 / 32.28 / 26.74 |
| melddb | fresh | start | 2 | 291.15 | 49827840 | 56.61 / 48.90 / 28.54 |
| melddb | fresh | start-plus-end | 0 | 1268.05 | 69271552 | 88.27 / 85.61 / 85.63 |
| melddb | fresh | start-plus-end | 1 | 1348.62 | 69271552 | 85.81 / 87.17 / 91.68 |
| melddb | fresh | start-plus-end | 2 | 1764.79 | 69271552 | 40.83 / 45.93 / 38.33 |
| postgres | analyzed | covering-end | 0 | 555.86 | 53961231 | 1218.58 / 1037.89 / 985.75 |
| postgres | analyzed | covering-end | 1 | 1209.19 | 53961231 | 1864.68 / 1629.12 / 1523.15 |
| postgres | analyzed | covering-end | 2 | 538.03 | 53961231 | 1258.39 / 1058.92 / 1034.01 |
| postgres | analyzed | covering-start | 0 | 517.80 | 53961231 | 1083.58 / 924.90 / 908.28 |
| postgres | analyzed | covering-start | 1 | 596.09 | 53961231 | 1292.72 / 1003.84 / 988.63 |
| postgres | analyzed | covering-start | 2 | 552.40 | 53961231 | 1216.94 / 964.93 / 1012.82 |
| postgres | analyzed | start | 0 | 407.93 | 53142031 | 1228.97 / 993.89 / 1004.86 |
| postgres | analyzed | start | 1 | 481.74 | 53142031 | 1261.97 / 989.88 / 1039.16 |
| postgres | analyzed | start | 2 | 442.94 | 53142031 | 1072.72 / 915.75 / 1031.38 |
| postgres | analyzed | start-plus-end | 0 | 4546.94 | 55648783 | 1244.34 / 1069.76 / 938.86 |
| postgres | analyzed | start-plus-end | 1 | 875.31 | 55648783 | 1215.03 / 1056.61 / 1016.48 |
| postgres | analyzed | start-plus-end | 2 | 926.42 | 55648783 | 612.47 / 444.12 / 483.55 |
| postgres | fresh | covering-end | 0 | 694.30 | 53892787 | 658.83 / 531.64 / 486.01 |
| postgres | fresh | covering-end | 1 | 538.49 | 53892787 | 1070.60 / 1050.45 / 912.64 |
| postgres | fresh | covering-end | 2 | 573.45 | 53892787 | 1213.59 / 1006.93 / 956.71 |
| postgres | fresh | covering-start | 0 | 568.37 | 53892787 | 1125.43 / 1005.94 / 1023.42 |
| postgres | fresh | covering-start | 1 | 615.23 | 53892787 | 589.99 / 419.43 / 455.94 |
| postgres | fresh | covering-start | 2 | 578.21 | 53892787 | 1178.90 / 1008.67 / 993.34 |
| postgres | fresh | start | 0 | 451.15 | 53073587 | 1307.48 / 995.44 / 1026.49 |
| postgres | fresh | start | 1 | 461.35 | 53073587 | 701.95 / 441.24 / 427.86 |
| postgres | fresh | start | 2 | 429.08 | 53073587 | 1215.02 / 1054.51 / 1172.98 |
| postgres | fresh | start-plus-end | 0 | 951.18 | 55580339 | 1155.83 / 985.51 / 980.82 |
| postgres | fresh | start-plus-end | 1 | 973.11 | 55580339 | 1374.47 / 1121.87 / 1095.91 |
| postgres | fresh | start-plus-end | 2 | 1102.20 | 55580339 | 1367.28 / 1019.45 / 1099.87 |
| sqlite | analyzed | covering-end | 0 | 935.94 | 55508992 | 77.59 / 87.87 / 63.81 |
| sqlite | analyzed | covering-end | 1 | 1652.44 | 55508992 | 126.64 / 134.71 / 129.92 |
| sqlite | analyzed | covering-end | 2 | 867.74 | 55508992 | 60.88 / 59.41 / 60.83 |
| sqlite | analyzed | covering-start | 0 | 816.92 | 55508992 | 69.99 / 64.19 / 69.33 |
| sqlite | analyzed | covering-start | 1 | 1143.79 | 55508992 | 61.70 / 80.26 / 76.70 |
| sqlite | analyzed | covering-start | 2 | 975.56 | 55508992 | 78.55 / 79.44 / 55.09 |
| sqlite | analyzed | start | 0 | 678.28 | 49831936 | 80.30 / 30.65 / 25.71 |
| sqlite | analyzed | start | 1 | 609.97 | 49831936 | 63.45 / 92.65 / 65.91 |
| sqlite | analyzed | start | 2 | 753.59 | 49831936 | 65.44 / 75.49 / 68.75 |
| sqlite | analyzed | start-plus-end | 0 | 1096.97 | 69275648 | 105.69 / 102.02 / 101.59 |
| sqlite | analyzed | start-plus-end | 1 | 1379.20 | 69275648 | 86.01 / 87.63 / 85.11 |
| sqlite | analyzed | start-plus-end | 2 | 2214.43 | 69275648 | 81.82 / 74.33 / 84.23 |
| sqlite | fresh | covering-end | 0 | 729.22 | 55504896 | 82.59 / 66.84 / 66.72 |
| sqlite | fresh | covering-end | 1 | 931.15 | 55504896 | 31.16 / 70.00 / 30.99 |
| sqlite | fresh | covering-end | 2 | 985.39 | 55504896 | 70.91 / 68.25 / 79.94 |
| sqlite | fresh | covering-start | 0 | 849.55 | 55504896 | 80.73 / 79.98 / 76.74 |
| sqlite | fresh | covering-start | 1 | 829.19 | 55504896 | 67.13 / 67.91 / 65.59 |
| sqlite | fresh | covering-start | 2 | 879.38 | 55504896 | 71.12 / 56.08 / 70.30 |
| sqlite | fresh | start | 0 | 331.26 | 49827840 | 28.92 / 30.71 / 27.18 |
| sqlite | fresh | start | 1 | 314.37 | 49827840 | 29.44 / 27.58 / 28.35 |
| sqlite | fresh | start | 2 | 334.48 | 49827840 | 49.43 / 37.82 / 37.77 |
| sqlite | fresh | start-plus-end | 0 | 1064.64 | 69271552 | 84.37 / 84.95 / 85.61 |
| sqlite | fresh | start-plus-end | 1 | 1185.83 | 69271552 | 90.60 / 85.37 / 82.10 |
| sqlite | fresh | start-plus-end | 2 | 1416.85 | 69271552 | 94.37 / 37.19 / 100.66 |

## Measurement sessions

3 session(s); continuity was checked within each session.
Pauses can change host load and OS cache state. Cross-session ratios are observations,
not uninterrupted paired measurements. Check raw samples before choosing a layout.

| Session | Treatments (1-based) | Started UTC | Ended UTC |
| --- | --- | --- | --- |
| 5cb33e8c2400f3c61c14d6b40e0b0979 | 1-21 | 2026-11-18T20:42:43.216266+00:00 | 2026-11-18T22:01:42.150049+00:00 |
| 1524f1491e1263543903f99d0ea41768 | 22-65 | 2026-11-20T15:10:18.342125+00:00 | 2026-11-20T17:06:51.722674+00:00 |
| d91bcbfaf3a864d377d0f3d3e526c6bc | 66-72 | 2026-11-20T17:58:03.014784+00:00 | 2026-11-20T18:27:48.628608+00:00 |

Treatments whose start-only baseline was measured in another session:
- postgres / analyzed / covering-end / round 1
- sqlite / analyzed / covering-end / round 1
- postgres / analyzed / start-plus-end / round 0
- postgres / analyzed / covering-start / round 0
- postgres / fresh / covering-end / round 1
- melddb / analyzed / covering-start / round 1
- sqlite / fresh / covering-end / round 2
- melddb / fresh / start-plus-end / round 1
- postgres / fresh / start-plus-end / round 0
- postgres / fresh / covering-start / round 0
- melddb / fresh / covering-end / round 2
- postgres / fresh / start-plus-end / round 2
- sqlite / analyzed / start-plus-end / round 0
- sqlite / fresh / covering-end / round 1
- sqlite / analyzed / covering-start / round 0
- melddb / fresh / covering-start / round 0
- postgres / analyzed / covering-start / round 2
- melddb / analyzed / covering-start / round 2
- postgres / fresh / covering-start / round 2
- melddb / analyzed / start-plus-end / round 0
- melddb / analyzed / covering-start / round 0
- sqlite / analyzed / covering-end / round 0
- melddb / fresh / covering-end / round 0
- postgres / analyzed / covering-start / round 1
- postgres / fresh / covering-end / round 2
- melddb / analyzed / start-plus-end / round 2
- melddb / analyzed / covering-end / round 0
- postgres / analyzed / start-plus-end / round 2
- sqlite / analyzed / start-plus-end / round 2
- sqlite / analyzed / start-plus-end / round 1
- sqlite / fresh / start-plus-end / round 1

SQLite allocation is whole-database pages and excludes WAL; per-index bytes are unavailable.
PostgreSQL per-index allocations and all plans, settings and raw samples are in report.json.
No universal index or memory default is implied. This does not qualify stored payloads.
