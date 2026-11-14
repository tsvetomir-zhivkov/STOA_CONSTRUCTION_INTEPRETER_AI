/** Serial imports with generation ownership. A removed session can never publish. */
export class FileSession {
  constructor(onState = () => {}) {
    this.generation = 0;
    this.state = 'EMPTY';
    this.tail = Promise.resolve();
    this.onState = onState;
    this.controller = new AbortController();
  }
  set(state) { this.state = state; this.onState(state); }
  invalidate() {
    this.set('REMOVING');
    this.controller.abort();
    this.controller = new AbortController();
    this.generation++;
    // An unresolved old file read must not block the next session.
    this.tail = Promise.resolve();
  }
  run(work, hasSources) {
    const generation = this.generation;
    const signal = this.controller.signal;
    const current = () => generation === this.generation && !signal.aborted;
    const task = this.tail.catch(() => {}).then(async () => {
      if (!current()) return [];
      this.set('UPLOADING');
      try {
        const result = await work({ current, signal, processing: () => current() && this.set('PROCESSING') });
        if (current()) this.set(hasSources() ? 'READY' : result.some(r => !r.ok) ? 'ERROR' : 'EMPTY');
        return current() ? result : [];
      } catch (error) {
        if (current()) this.set(hasSources() ? 'READY' : 'ERROR');
        if (!current()) return [];
        throw error;
      }
    });
    this.tail = task;
    return task;
  }
}
