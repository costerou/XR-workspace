import re,sys
src=open(sys.argv[1]).read().split('\n')
text='\n'.join(src[10:180])
names={'kQuadVertex':'QUAD_VERTEX','kTextFragment':'TEXT_FRAGMENT','kGuideFragment':'GUIDE_FRAGMENT','kWarpVertex':'WARP_VERTEX',
'kSampleGuide':None,'kReconstructFragment':'RECONSTRUCT_FRAGMENT','kResolveFragment':'RESOLVE_FRAGMENT','kWarpFragment':'WARP_FRAGMENT',
'kPointerVertex':'POINTER_VERTEX','kPointerFragment':'POINTER_FRAGMENT'}
pat=re.compile(r'(?:const char\*|const std::string)\s+(k\w+)\s*=(.*?);\s*(?=\n|$)',re.S)
tok=re.compile(r'"((?:[^"\\]|\\.)*)"|(XR_PROJECT)|(kSampleGuide)')
out=[]
for m in pat.finditer(text):
    n,body=m.group(1),m.group(2)
    parts=[]
    for t in tok.finditer(body):
        if t.group(1) is not None: parts.append('"%s"'%t.group(1))
        elif t.group(2): parts.append('xr_project!()')
        else: parts.append('sample_guide!()')
    inner=',\n    '.join(parts)
    if names[n] is None:
        out.append('macro_rules! sample_guide {\n    () => {\n        concat!(\n            %s\n        )\n    };\n}'%(',\n            '.join(parts)))
    else:
        out.append('const %s: &str = concat!(\n    %s\n);'%(names[n],inner))
print('\n'.join(out))
