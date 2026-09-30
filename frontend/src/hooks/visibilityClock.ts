// 连续可见条件中断时重新计时，不累计跨后台或离屏的片段。
export class VisibilityClock {
  private timer: ReturnType<typeof setTimeout> | undefined
  private complete = false
  constructor(private submit: () => boolean | void) {}
  update(ratio: number, foreground: boolean) {
    if (ratio < .5 || !foreground) {
      this.cancel()
    } else if (!this.complete && this.timer === undefined) {
      this.timer = setTimeout(() => {
        this.timer = undefined
        this.complete = this.submit() !== false
      }, 1000)
    }
  }
  cancel() {
    if (this.timer !== undefined) clearTimeout(this.timer)
    this.timer = undefined
  }
}
