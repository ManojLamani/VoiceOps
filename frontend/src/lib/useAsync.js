import { useCallback, useEffect, useState } from 'react';

/** Load data with loading/error state and a reload function. `deps` re-run the loader. */
export default function useAsync(loader, deps = []) {
  const [state, setState] = useState({ data: null, loading: true, error: null });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(() => loader(), deps);
  const reload = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }));
    return run()
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((e) => setState({ data: null, loading: false, error: e.message }));
  }, [run]);
  // Re-fetch in the background: keeps showing the current data, and keeps it if the request fails.
  const refresh = useCallback(() => run().then((data) => setState({ data, loading: false, error: null }), () => {}), [run]);
  useEffect(() => {
    let live = true;
    run()
      .then((data) => live && setState({ data, loading: false, error: null }))
      .catch((e) => live && setState({ data: null, loading: false, error: e.message }));
    return () => { live = false; };
  }, [run]);
  return { ...state, reload, refresh };
}
