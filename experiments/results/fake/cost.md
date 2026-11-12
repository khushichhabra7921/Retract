# M8 cost split (FakeLLM, auto, serial, seeds [10, 11, 12, 13, 14])

Medians over seeds with [p25, p75]. Re-derivation uses FakeLLM, so its time is a lower bound; a hosted LLM adds its latency per call.

| scenario | write µs Retract / raw | read µs guarded / raw | op s | re-derive s | probes s | rest s | LLM calls |
|---|---|---|---|---|---|---|---|
| priya | 7389 [7357, 7571] / 132 [118, 139] | 1191 [732, 1394] / 641 [434, 782] | 0.306 [0.277, 0.315] | 0.000 [0.000, 0.000] | 0.129 [0.107, 0.139] | 0.175 [0.170, 0.177] | 4 [4, 4] |
| poisoned_doc | 7923 [7409, 7965] / 146 [124, 150] | 1206 [712, 1428] / 621 [432, 745] | 1.941 [1.801, 2.088] | 0.000 [0.000, 0.000] | 0.063 [0.046, 0.067] | 1.909 [1.737, 2.022] | 54 [50, 56] |
