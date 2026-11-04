#!/usr/bin/env python3
"""Build QA CPU serializer from existing native target link dependencies, no CMake edits."""
import argparse
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();build=args.build.resolve();output=args.output.resolve()
    link=shlex.split((build/'CMakeFiles/video_readback_test.dir/link.txt').read_text())
    with tempfile.TemporaryDirectory(prefix='xr-qa-native-report-') as temp:
        obj=Path(temp)/'report.o'
        compile=[link[0],'-std=c++20','-I'+str(ROOT/'spike/include'),'-I'+str(ROOT/'spike/src'),
                 '-c',str(ROOT/'tools/xr_options_evidence/native_report.cpp'),'-o',str(obj)]
        subprocess.run(compile,check=True,timeout=60)
        replaced=False
        for n,arg in enumerate(link):
            if arg.endswith('video_readback_test.cpp.o'):link[n]=str(obj);replaced=True
        if not replaced:raise ValueError('unknown native target object layout')
        link=[a for a in link if not a.startswith('-Wl,--dependency-file=')]
        link[link.index('-o')+1]=str(output)
        subprocess.run(link,cwd=build,check=True,timeout=60)


if __name__=='__main__':main()
