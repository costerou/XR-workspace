"""Compile exact production pair-boundary methods without loading Hyprland."""
import pathlib
import sys
source = pathlib.Path(sys.argv[1]).read_text()
methods = []
for signature in ("    PHLMONITOR resolve(", "    Handle selectPairOutput(", "    PairObservation observePairOutput(",
                  "    bool usable("):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    methods.append(source[start:end])
broker_start = source.index("struct Broker {")
broker_end = source.index("\nstd::unique_ptr<Broker>", broker_start)
pathlib.Path(sys.argv[2]).with_name("ownership_pair_broker.inc").write_text(source[broker_start:broker_end])
pathlib.Path(sys.argv[2]).write_text("\n".join(methods) + "\n")
