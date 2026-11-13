import ast,sys,pathlib,re
files=sys.argv[1:]
total=0
for fp in files:
    p=pathlib.Path(fp);src=p.read_text();lines=src.split('\n');t=ast.parse(src)
    # enclosing function names
    parent={}
    for n in ast.walk(t):
        for c in ast.iter_child_nodes(n): parent[c]=n
    def enclosing(n):
        names=[]
        while n in parent:
            n=parent[n]
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): names.append(n.name)
        return '.'.join(reversed(names)) or '<module>'
    mod=p.stem
    edits=[]  # (lineno_index, new_lines, replace_count)
    for n in ast.walk(t):
        if isinstance(n,ast.ExceptHandler) and n.type is not None and isinstance(n.type,ast.Name) and n.type.id=='Exception' and len(n.body)==1 and isinstance(n.body[0],(ast.Pass,ast.Continue)):
            where=f'{mod}.{enclosing(n)}'
            b=n.body[0]
            call=f'_swallowed_note("{where}")'
            if b.lineno==n.lineno:        # one-liner:  except Exception: pass
                line=lines[n.lineno-1]
                m=re.match(r'^(\s*)(except Exception(?: as \w+)?):\s*(pass|continue)\s*(#.*)?$',line)
                if not m: continue
                ind=m.group(1)
                edits.append((n.lineno-1,[f'{ind}{m.group(2)}:',f'{ind}    {call}',f'{ind}    {m.group(3)}'],1))
            else:
                ind=re.match(r'^(\s*)',lines[b.lineno-1]).group(1)
                edits.append((b.lineno-1,[f'{ind}{call}'],0))
    if not edits: continue
    for idx,new,rep in sorted(edits,reverse=True):
        lines[idx:idx+rep]=new+([] ) if rep else new   # rep==1 replace; rep==0 insert before
        if not rep: pass
    # fix: rep==0 should insert before existing line (handled by slice assignment above with zero length)
    s='\n'.join(lines)
    # add import after the last top-level __future__/docstring: before first non-future import
    t2=ast.parse(s)
    pos=None
    for n in t2.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)) and not (isinstance(n,ast.ImportFrom) and n.module=='__future__'):
            pos=n.lineno-1;break
    rel='.'.join(['']*1) 
    pkg=p.parent.name
    imp='from .swallowed import note as _swallowed_note' if pkg=='core' else 'from ..core.swallowed import note as _swallowed_note'
    L=s.split('\n');L.insert(pos,imp);s='\n'.join(L)
    ast.parse(s)
    p.write_text(s);total+=len(edits);print(fp,len(edits))
print('total',total)
