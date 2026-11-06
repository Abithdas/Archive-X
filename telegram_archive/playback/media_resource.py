"""Shared random-access media resource for Archive X playback consumers."""
from __future__ import annotations
import asyncio
from typing import Any

class ArchiveXMediaResource:
    """Random-access view over one Telegram-backed media object."""
    def __init__(self, engine: Any, vkey: tuple[int,int], message: Any):
        self.engine=engine; self.vkey=(int(vkey[0]),int(vkey[1])); self.message=message
    @property
    def size(self): return int(getattr(getattr(self.message,'file',None),'size',0) or 0)
    @property
    def duration(self): return float(getattr(getattr(self.message,'file',None),'duration',0) or 0.0)
    async def read(self, offset:int, length:int, *, priority:int=0, epoch:int=0, kind:str='playback')->bytes:
        offset=max(0,int(offset)); length=max(0,int(length)); end=min(self.size,offset+length)
        if length<=0 or offset>=self.size: return b''
        chunk=int(self.engine.segment_size); first=offset//chunk; last=(end-1)//chunk
        parts=await asyncio.gather(*(self.engine.get_segment(self.vkey,self.message,seg,priority,epoch,kind=kind) for seg in range(first,last+1)))
        out=bytearray()
        for seg,data in zip(range(first,last+1),parts):
            base=seg*chunk; a=max(0,offset-base); b=min(len(data),end-base)
            if b>a: out.extend(data[a:b])
        return bytes(out)
    async def read_range(self,start:int,end:int,**kwargs)->bytes:
        start=max(0,int(start)); end=min(self.size-1,int(end))
        return b'' if end<start else await self.read(start,end-start+1,**kwargs)
