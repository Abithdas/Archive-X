import { useEffect, useState } from 'react';
/** Value that follows `value` after it has been stable for `ms` (keeps typing from firing a request per key). */
export function useDebounced(value, ms) {
    const [v, setV] = useState(value);
    useEffect(() => {
        const t = setTimeout(() => setV(value), ms);
        return () => clearTimeout(t);
    }, [value, ms]);
    return v;
}
