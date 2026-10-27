// dom free for node tests

// parallel sends would fail revision checks
export function createSaveQueue({ send, onConflict = () => {} }) {
  let inflight = null;
  let queued = null;

  async function run(batch) {
    inflight = batch;
    let error = null;
    try {
      error = await send();
    } catch (thrown) {
      error = thrown instanceof Error ? thrown : new Error(String(thrown));
    }
    inflight = null;
    if (error?.conflict) {
      // queued saves would resend the reload
      const dropped = [batch, queued].filter(Boolean);
      queued = null;
      onConflict(dropped.reduce((count, each) => count + each.changes, 0), error);
      for (const each of dropped) for (const resolve of each.waiters) resolve(error);
      return;
    }
    for (const resolve of batch.waiters) resolve(error || null);
    if (queued) {
      const next = queued;
      queued = null;
      run(next);
    }
  }

  return {
    // resolves to the error or null
    save(changes = 1) {
      return new Promise((resolve) => {
        if (!inflight) return run({ waiters: [resolve], changes });
        queued ||= { waiters: [], changes: 0 };
        queued.waiters.push(resolve);
        queued.changes += changes;
      });
    },
    get busy() { return Boolean(inflight); },
  };
}

export function conflictMessage(count) {
  const lost = count > 1 ? `your last ${new Intl.NumberFormat().format(count)} changes weren't saved` : "your last change wasn't saved";
  return `Your profile changed in another tab. The latest version is loaded and ${lost}.`;
}
