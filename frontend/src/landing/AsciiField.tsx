import { useEffect, useRef } from "react";
import { GLITCH, SCALES } from "./ascii";

// Сетка символов на canvas во весь экран. Узор из весов в шахматном порядке
// шагает по диагонали снизу-слева вверх-вправо. Шаг — это не плавный сдвиг,
// а перезапись: каждая изменившаяся клетка пару кадров показывает случайный
// символ и только потом встаёт на своё место. Рядом с курсором символы ярче
// и «сбоят» сами по себе.

const FONT_PX = 12;
const ROW_PX = 15;
const SPRITE_W = SCALES[0].length;
const SPRITE_H = SCALES.length;
const PERIOD_X = 22; // шаг узора по горизонтали, клеток
const PERIOD_Y = 8; // и по вертикали; нечётные ряды сдвинуты на PERIOD_X/2
const STEP_MS = 650; // как часто узор сдвигается на клетку
const CURSOR_R = 140; // px
const BASE = "rgba(63, 134, 255, 0.62)";
const BRIGHT = "rgba(160, 200, 255, 1)";
const GLITCH_COLOR = "rgba(110, 170, 255, 0.85)";

const mod = (a: number, n: number) => ((a % n) + n) % n;

function patternAt(col: number, row: number, tx: number, ty: number): string {
  const r = row + ty; // узор едет вверх
  const tileRow = Math.floor(r / PERIOD_Y);
  const c = col - tx + (mod(tileRow, 2) ? PERIOD_X / 2 : 0); // и вправо
  const y = mod(r, PERIOD_Y);
  const x = mod(c, PERIOD_X);
  if (y >= SPRITE_H || x >= SPRITE_W) return " ";
  return SCALES[y][x];
}

export default function AsciiField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const font = `${FONT_PX}px ui-monospace, "SF Mono", Menlo, Consolas, monospace`;

    let cols = 0;
    let rows = 0;
    let cellW = 7;
    let target: string[] = []; // где символ должен стоять
    let shown: string[] = []; // что нарисовано сейчас
    let scramble = new Int8Array(0); // сколько кадров клетка ещё «перезаписывается»
    let delay = new Int8Array(0); // через сколько кадров начнёт
    let bright = new Uint8Array(0);
    let tx = 0;
    let ty = 0;
    let steps = 0;
    const mouse = { x: -9999, y: -9999 };

    const drawCell = (i: number, ch: string, color: string) => {
      const x = (i % cols) * cellW;
      const y = Math.floor(i / cols) * ROW_PX;
      ctx.clearRect(x, y, cellW, ROW_PX);
      if (ch !== " ") {
        ctx.fillStyle = color;
        ctx.fillText(ch, x, y + ROW_PX - 3);
      }
    };

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = window.innerWidth;
      const h = window.innerHeight;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = font;
      cellW = Math.ceil(ctx.measureText("M").width);
      cols = Math.ceil(w / cellW);
      rows = Math.ceil(h / ROW_PX);
      const n = cols * rows;
      target = new Array(n);
      shown = new Array(n);
      scramble = new Int8Array(n);
      delay = new Int8Array(n);
      bright = new Uint8Array(n);
      ctx.clearRect(0, 0, w, h);
      for (let i = 0; i < n; i++) {
        target[i] = patternAt(i % cols, Math.floor(i / cols), tx, ty);
        shown[i] = target[i];
        drawCell(i, shown[i], BASE);
      }
    };
    resize();
    if (reduced) return;

    const step = () => {
      steps += 1;
      tx += 1; // вправо на клетку каждый шаг,
      if (steps % 2 === 0) ty += 1; // вверх — через шаг (клетки вдвое выше, чем шире)
      for (let i = 0; i < target.length; i++) {
        const next = patternAt(i % cols, Math.floor(i / cols), tx, ty);
        if (next !== target[i]) {
          target[i] = next;
          delay[i] = Math.floor(Math.random() * 2);
          scramble[i] = 1 + Math.floor(Math.random() * 2);
        }
      }
    };

    let frame = 0;
    let lastStep = performance.now();
    let lastFrame = 0;
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      if (now - lastFrame < 33) return; // ~30 кадров/с хватает
      lastFrame = now;
      if (now - lastStep >= STEP_MS) {
        lastStep = now;
        step();
      }
      const mc = mouse.x / cellW;
      const mr = mouse.y / ROW_PX;
      const rc = CURSOR_R / cellW;
      const rr = CURSOR_R / ROW_PX;
      for (let i = 0; i < target.length; i++) {
        const c = i % cols;
        const r = (i - c) / cols;
        const dx = (c - mc) / rc;
        const dy = (r - mr) / rr;
        const near = dx * dx + dy * dy < 1;
        const hasGlyph = target[i] !== " " || shown[i] !== " ";
        // рядом с курсором символы сбоят сами по себе
        if (near && target[i] !== " " && scramble[i] === 0 && Math.random() < 0.06) {
          scramble[i] = 2 + Math.floor(Math.random() * 2);
        }
        if (delay[i] > 0) { delay[i] -= 1; continue; }
        const isBright = near ? 1 : 0;
        if (scramble[i] > 0) {
          scramble[i] -= 1;
          const ch = scramble[i] === 0 ? target[i] : GLITCH[(Math.random() * GLITCH.length) | 0];
          shown[i] = ch;
          bright[i] = isBright;
          drawCell(i, ch, scramble[i] === 0 ? (isBright ? BRIGHT : BASE) : GLITCH_COLOR);
        } else if (hasGlyph && (shown[i] !== target[i] || bright[i] !== isBright)) {
          shown[i] = target[i];
          bright[i] = isBright;
          drawCell(i, shown[i], isBright ? BRIGHT : BASE);
        }
      }
    };
    frame = requestAnimationFrame(tick);

    const onMove = (e: PointerEvent) => { mouse.x = e.clientX; mouse.y = e.clientY; };
    const onLeave = () => { mouse.x = -9999; mouse.y = -9999; };
    window.addEventListener("pointermove", onMove);
    document.addEventListener("pointerleave", onLeave);
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvasRef} className="ascii-field" aria-hidden="true" />;
}
