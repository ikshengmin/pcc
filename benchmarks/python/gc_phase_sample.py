import gc
from pcc.extern import c_int64, extern

_phase = extern("pcc_exp_pause_value", (c_int64,), c_int64)
_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)

def main():
    gc.disable()
    root = []
    for i in range(24):
        value = []
        value.append(value)
        root.append(value)
    del root
    gc.collect()
    gc.collect()
    print("gc-pause-stw", _telemetry(119), _telemetry(120), _telemetry(121))
    i = 0
    value = _phase(i)
    while value >= 0:
        print("gc-pause-slot", i, value)
        i += 1
        value = _phase(i)
    if _phase(-1) != -1 or value != -1:
        raise RuntimeError("phase counter bounds")

if __name__ == "__main__":
    main()
