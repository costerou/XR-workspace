#!/usr/bin/env python3
"""Build QA CPU serializer from existing native target link dependencies, no CMake edits."""
import argparse
from pathlib import Path
import shlex
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]


def link_command(build):
    """The real link line of video_readback_test: from Makefiles' link.txt, or from Ninja, which has no such file."""
    saved=build/'CMakeFiles/video_readback_test.dir/link.txt'
    if saved.exists():return shlex.split(saved.read_text())
    lines=subprocess.run(['ninja','-C',str(build),'-t','commands','video_readback_test'],check=True,timeout=60,capture_output=True,text=True).stdout
    link=shlex.split(lines.strip().splitlines()[-1])
    return link[2:-2] if link[:2]==[':','&&'] and link[-2:]==['&&',':'] else link


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-root',type=Path,default=ROOT,help='native source checkout matching built libraries')
    args=p.parse_args();build=args.build.resolve();output=args.output.resolve();native=args.source_root.resolve()
    link=link_command(build)
    with tempfile.TemporaryDirectory(prefix='xr-qa-native-report-') as temp:
        obj=Path(temp)/'report.o'
        compile=[link[0],'-std=c++20','-I'+str(native/'spike/include'),'-I'+str(native/'spike/src'),
                 '-c',str(ROOT/'tools/xr_options_evidence/native_report.cpp'),'-o',str(obj)]
        subprocess.run(compile,check=True,timeout=60)
        replaced=False
        for n,arg in enumerate(link):
            if arg.endswith('video_readback_test.cpp.o'):link[n]=str(obj);replaced=True
        if not replaced:raise ValueError('unknown native target object layout')
        # The fast linker comes through a per-user shim, which a private HOME cannot run; the default linker does the same job.
        link=[a for a in link if not a.startswith(('-Wl,--dependency-file=','-B','-fuse-ld='))]
        link[link.index('-o')+1]=str(output)
        subprocess.run(link,cwd=build,check=True,timeout=60)


if __name__=='__main__':main()
