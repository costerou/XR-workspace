#!/usr/bin/env python3
"""Symbolise a malloc_count.c trace (XR_MALLOC_TRACE): top allocation call stacks with counts.

Usage: malloc_report.py TRACE [--top N] [--depth D] [--seconds S]
Uses readelf/nm from binutils; addresses inside a mapped ELF file resolve to the nearest preceding symbol.
"""
import bisect
import collections
import re
import subprocess
import sys


def elf_symbols(path, cache={}):
    if path in cache:
        return cache[path]
    segments, symbols = [], []
    try:
        headers = subprocess.run(['readelf', '-lW', path], capture_output=True, text=True, check=False).stdout
        for line in headers.splitlines():
            parts = line.split()
            if parts and parts[0] == 'LOAD':
                offset, vaddr, filesz = int(parts[1], 16), int(parts[2], 16), int(parts[4], 16)
                segments.append((offset, vaddr, filesz))
        for flags in (['-C', '--defined-only'], ['-C', '-D', '--defined-only']):
            out = subprocess.run(['nm', *flags, path], capture_output=True, text=True, check=False).stdout
            for line in out.splitlines():
                m = re.match(r'([0-9a-f]+) [tTwWiu] (.+)', line)
                if m:
                    symbols.append((int(m.group(1), 16), m.group(2)))
    except OSError:
        pass
    symbols.sort()
    cache[path] = (segments, [a for a, _ in symbols], [n for _, n in symbols])
    return cache[path]


def resolve(addr, maps):
    for start, end, offset, path in maps:
        if start <= addr < end:
            file_off = addr - start + offset
            segments, addrs, names = elf_symbols(path)
            vaddr = file_off
            for seg_off, seg_vaddr, size in segments:
                if seg_off <= file_off < seg_off + size:
                    vaddr = file_off - seg_off + seg_vaddr
                    break
            i = bisect.bisect_right(addrs, vaddr) - 1
            short = path.rsplit('/', 1)[-1]
            if i >= 0:
                name = names[i]
                return f'{short}:{name[:110]}'
            return f'{short}+{file_off:#x}'
    return hex(addr)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    top = int(args[args.index('--top') + 1]) if '--top' in args else 25
    depth = int(args[args.index('--depth') + 1]) if '--depth' in args else 6
    seconds = float(args[args.index('--seconds') + 1]) if '--seconds' in args else 0
    sites, maps, in_maps = [], [], False
    with open(args[0]) as trace:
        for line in trace:
            if line.startswith('maps'):
                in_maps = True
                continue
            if in_maps:
                parts = line.split()
                if len(parts) >= 6 and parts[5].startswith('/'):
                    start, end = (int(x, 16) for x in parts[0].split('-'))
                    maps.append((start, end, int(parts[2], 16), parts[5]))
            elif line.startswith('site '):
                parts = line.split()
                sites.append((int(parts[1]), [int(a, 16) for a in parts[2:]]))
    total = sum(c for c, _ in sites)
    grouped = collections.Counter()
    for count, frames in sites:
        # Skip the allocator layers (shim, libstdc++ operator new) so the first frame is the caller that allocates.
        names = [resolve(a, maps) for a in frames]
        while names and (names[0].startswith('libxr_malloc_count') or 'operator new' in names[0] or names[0].startswith('libc.so')):
            names.pop(0)
        grouped[tuple(names[:depth])] += count
    rate = f' ({total / seconds:.0f}/s)' if seconds else ''
    print(f'allocations recorded: {total}{rate}')
    for stack, count in grouped.most_common(top):
        share = 100.0 * count / total if total else 0
        per_s = f' {count / seconds:8.0f}/s' if seconds else ''
        print(f'{count:8d} {share:5.1f}%{per_s}  ' + '\n                        <- '.join(stack))
    return 0


if __name__ == '__main__':
    sys.exit(main())
