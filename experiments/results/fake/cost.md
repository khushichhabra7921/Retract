# M8 cost split (FakeLLM, auto, serial, seeds [10, 11, 12, 13, 14])

Medians over seeds with [p25, p75]. Re-derivation uses FakeLLM, so its time is a lower bound; a hosted LLM adds its latency per call.

| scenario | write µs Retract / raw | read µs guarded / raw | op s | re-derive s | probes s | rest s | LLM calls |
|---|---|---|---|---|---|---|---|
| priya | 5161 [5015, 5391] / 126 [125, 134] | 1387 [1370, 1395] / 728 [686, 760] | 0.318 [0.312, 0.329] | 0.000 [0.000, 0.000] | 0.184 [0.172, 0.190] | 0.141 [0.131, 0.144] | 4 [4, 4] |
| poisoned_doc | 5356 [5337, 5499] / 128 [126, 132] | 1453 [1433, 1465] / 775 [711, 820] | 1.435 [1.338, 1.515] | 0.000 [0.000, 0.000] | 0.086 [0.081, 0.088] | 1.349 [1.253, 1.431] | 54 [50, 56] |
