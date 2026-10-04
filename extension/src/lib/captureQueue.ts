/** Durable writes are acknowledged only after the backend commits them. No model work here. */
export type PendingWrite = { id: string; path: string; body: unknown };
export type QueueStorage = { get(key: string): Promise<Record<string, unknown>>; set(value: Record<string, unknown>): Promise<void> };
export class CaptureQueue {
  private serial: Promise<unknown> = Promise.resolve();
  constructor(private key: string, private storage: QueueStorage, private send: (path: string, body: unknown) => Promise<unknown>) {}
  private async read(): Promise<PendingWrite[]> { return ((await this.storage.get(this.key))[this.key] as PendingWrite[] | undefined) ?? []; }
  private run<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.serial.then(operation, operation);
    this.serial = next.catch(() => undefined);
    return next;
  }
  enqueue(write: PendingWrite): Promise<void> {
    return this.run(async () => {
      const writes = await this.read();
      const old = writes.find(x => x.id === write.id);
      if (old && JSON.stringify(old) !== JSON.stringify(write)) throw new Error('A capture identity was reused.');
      if (!old) await this.storage.set({[this.key]: [...writes, write]});
    });
  }
  flush(): Promise<void> {
    return this.run(async () => {
      let writes = await this.read();
      while (writes.length) {
        await this.send(writes[0].path, writes[0].body);
        writes = writes.slice(1);
        await this.storage.set({[this.key]: writes});
      }
    });
  }
  pending(): Promise<number> { return this.run(async () => (await this.read()).length); }
}
export const captureTabMatches = (bound: number | null, sender: number | undefined, accepting: boolean) => accepting && bound !== null && bound === sender;
