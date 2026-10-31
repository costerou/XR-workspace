"""Compile production method bodies against offline compositor boundary doubles.

No plugin loader or compositor is linked/executed. This is source extraction, not
an assertion on source spelling: the C++ test asserts Registry lifecycle results.
"""
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text()
methods = []
for name in ("configure", "verified"):
    start = source.index(f"    bool {name}(")
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    methods.append(source[start:end])
pathlib.Path(sys.argv[2]).write_text("\n".join(methods) + "\n")
