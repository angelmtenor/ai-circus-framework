/**
 * The one animation loop every canvas scene runs on, so a tab left on screen while the
 * presenter talks never keeps a laptop's core busy (the fix networkScene.ts and
 * knowledgeGraphScene.ts made by hand, generalised — see commits 214ce1c and 9fe8df1).
 *
 * The scene draws in `frame(now)` and says what kind of motion it still has:
 * - `"moving"`: essential motion (a tween, playback, particles settling) — keeps running
 *   at up to `fps`;
 * - `"ambient"`: decorative motion (a slow spin, flights, a breathing glow) — runs at up to
 *   `ambientFps`, and rests `restAfterMs` after the last `wake()`;
 * - `"idle"`: nothing moves — the loop stops until the next `wake()` / `redraw()`.
 *
 * Whatever the scene says, nothing is drawn while the page or the element is hidden, and
 * the frame interval stretches when frames are expensive (a GPU-less machine rasterising a
 * HiDPI canvas) so animation always leaves the main thread at least half idle.
 */

export type Motion = "moving" | "ambient" | "idle";

export type SceneLoopOptions = {
  fps?: number;
  ambientFps?: number;
  restAfterMs?: number;
};

export const DEFAULT_REST_AFTER_MS = 15_000;

export class SceneLoop {
  private readonly frame: (now: number) => Motion;
  private readonly target: Element;
  private readonly observer: IntersectionObserver | null;
  private readonly frameMs: number;
  private readonly ambientFrameMs: number;
  private readonly restAfterMs: number;
  private raf = 0;
  private visible = true;
  private destroyed = false;
  private pending = true; // a redraw was asked for, whatever the motion
  private motion: Motion = "moving";
  private lastDraw = 0;
  private lastWake = performance.now();
  private drawMs = 0; // moving average of what a frame costs

  constructor(target: Element, frame: (now: number) => Motion, options: SceneLoopOptions = {}) {
    this.target = target;
    this.frame = frame;
    this.frameMs = 1000 / (options.fps ?? 30);
    this.ambientFrameMs = 1000 / (options.ambientFps ?? 20);
    this.restAfterMs = options.restAfterMs ?? DEFAULT_REST_AFTER_MS;
    document.addEventListener("visibilitychange", this.onVisibility);
    this.observer =
      typeof IntersectionObserver !== "undefined"
        ? new IntersectionObserver((entries) => {
            const wasVisible = this.visible;
            this.visible = entries.some((e) => e.isIntersecting);
            if (this.visible && !wasVisible) this.wake();
          })
        : null;
    this.observer?.observe(target);
    this.schedule();
  }

  /** The reader did something, or the view changed: redraw and let ambient motion run again. */
  readonly wake = (): void => {
    this.lastWake = performance.now();
    this.redraw();
  };

  /** Redraw once (a resize, new geometry) without restarting ambient motion that rests. */
  readonly redraw = (): void => {
    this.pending = true;
    this.schedule();
  };

  /** Milliseconds since the last `wake()` — e.g. to start an idle spin only after a pause. */
  idleFor(now: number): number {
    return now - this.lastWake;
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.observer?.disconnect();
    document.removeEventListener("visibilitychange", this.onVisibility);
  }

  private readonly onVisibility = (): void => {
    if (!document.hidden) this.wake();
  };

  private schedule(): void {
    if (!this.raf && !this.destroyed) this.raf = requestAnimationFrame(this.tick);
  }

  private wantsMotion(now: number): boolean {
    return this.motion === "moving" || (this.motion === "ambient" && now - this.lastWake < this.restAfterMs);
  }

  private readonly tick = (now: number): void => {
    this.raf = 0;
    // Resumed by the IntersectionObserver / visibilitychange, never by polling.
    if (document.hidden || !this.visible || this.target.isConnected === false) return;
    const pace = this.motion === "ambient" ? this.ambientFrameMs : this.frameMs;
    const interval = Math.max(pace, Math.min(250, 2 * this.drawMs));
    if (this.pending || (this.wantsMotion(now) && now - this.lastDraw >= interval - 1)) {
      this.pending = false;
      this.lastDraw = now;
      const started = performance.now();
      this.motion = this.frame(now);
      this.drawMs = 0.8 * this.drawMs + 0.2 * (performance.now() - started);
    }
    if (this.pending || this.wantsMotion(now)) this.schedule();
  };
}
