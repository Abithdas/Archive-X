import json,collections,sys
d=json.load(open('inventory.json'))
F={}
for f in d['functions']: F.setdefault(f['name'],f)
def closure(names,depth=2):
    seen=set(n for n in names if n in F);frontier=list(seen)
    for _ in range(depth):
        nxt=[]
        for n in frontier:
            for h in F[n]['ui_handlers']:
                if h in F and h not in seen: seen.add(h);nxt.append(h)
        frontier=nxt
    return seen
for h in d['html']:
    name=h['file'].split('/')[-1].replace('.html','')
    out=[f"# {h['file']}","## headings"]+[f"- {x['text']}" for x in h['headings']]
    out.append("## controls")
    names=set()
    for it in h['items']:
        hs=sorted({fn for v in it['handlers'].values() for fn in v});names|=set(hs)
        lab=(it['text'] or it['label'] or it['id'] or '').strip()[:60]
        out.append(f"- [{it['section'][:30]}] {it['tag']}{'#'+it['id'] if it['id'] else ''} \"{lab}\" -> {', '.join(hs)}")
    out.append("## dialogs: "+', '.join(h['dialogs']))
    cl=closure(names)
    api=collections.Counter(a for n in cl for a in F[n]['api'])
    out.append("## reachable functions ("+str(len(cl))+"): "+', '.join(sorted(cl)))
    out.append("## endpoints: "+', '.join(sorted(api)))
    open(f'raw-{name}.md','w').write('\n'.join(out))
    print(name,len(h['items']),'controls',len(cl),'functions',len(api),'endpoints')
