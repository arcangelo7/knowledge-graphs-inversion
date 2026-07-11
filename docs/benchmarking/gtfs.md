<!--
SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>

SPDX-License-Identifier: ISC
-->

# GTFS Bench

[GTFS Bench](https://github.com/oeg-upm/gtfs-bench) generates transit datasets at configurable scales.

## Run

```bash
make benchmark-gtfs I=10 S=1,5,10
```

`I` is the number of iterations. `S` is a comma-separated list of scales.
