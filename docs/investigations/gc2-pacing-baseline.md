# GC2 stock pacing arithmetic

The current threaded GC2 is on the shared tracing scheduler. Its start
threshold is the greater of 4 MiB and the last marked live bytes multiplied
by (pause−100)/100; an explicit positive override takes precedence.
This is allocation **debt** above the last completion, not an absolute
current heap goal. The separate step multiplier turns debt into work;
only completed marking work pays down allocated bytes.

The isolated Linux pcc0 test stops the actual GC2 world with automatic
GC disabled, supplies fixed last-mark and allocation snapshots, and calls
the production routines for threshold, budget, and payment. It covers
minimum threshold, a growing live-set target, fractional rounding,
positive override, budget minimum and no-credit zero work. No new
pacer was installed. Next collect real worker/assist counters and maximum
stop with identical allocation shapes at one and four mutators. Do not
confuse fixture arithmetic or an unpaired telemetry run with a throughput
or GC2 performance result.
