// hazard_worklet.js - AudioWorklet: down-mixes the microphone to mono and hands 4096-sample blocks to the page.
// Runs only while "listening for danger while the app is open" is on. Nothing is stored or sent anywhere.
class LgHazardCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(4096);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0];
    if (ch && ch.length && ch[0]) {
      const L = ch[0].length, C = ch.length;
      for (let i = 0; i < L; i++) {
        let s = 0;
        for (let c = 0; c < C; c++) s += ch[c][i];
        this.buf[this.n++] = s / C;
        if (this.n === this.buf.length) {
          this.port.postMessage(this.buf, [this.buf.buffer]);
          this.buf = new Float32Array(4096);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor('lg-hazard-capture', LgHazardCapture);
