import { useEffect, useRef } from "react";
import { GLITCH, SCALES } from "./ascii";

// Сетка символов на canvas во весь экран. Узор из весов в шахматном порядке
// шагает по диагонали снизу-слева вверх-вправо. Шаг — не плавный сдвиг,
// а перезапись: изменившиеся клетки с небольшим разбросом во времени
// показывают случайный символ и встают на место. К краям экрана весы
// гаснут. Рядом с курсором — мягкое пятно: символы светлее и изредка сбоят;
// граница пятна размыта и неровная, а само пятно догоняет курсор с инерцией.

const FONT_PX = 12;
const ROW_PX = 15;
const SPRITE_W = SCALES[0].length;
const SPRITE_H = SCALES.length;
const PERIOD_X = 28; // шаг узора по горизонтали, клеток
const PERIOD_Y = 10; // и по вертикали; нечётные ряды сдвинуты на PERIOD_X/2
const STEP_MS = 1800; // как часто узор сдвигается на клетку
const SPREAD_FRAMES = 8; // разброс начала перезаписи — волна, а не щелчок
const EDGE_FADE = 0.15; // доля экрана у каждого края, где весы гаснут
const CURSOR_R = 190; // px — радиус пятна (с мягким спадом)
const CURSOR_FOLLOW = 0.12; // инерция пятна: доля пути до курсора за кадр
const LEVELS = 12; // ступени яркости/затухания для кэша цветов

const BASE: [number, number, number, number] = [63, 134, 255, 0.7];
const BRIGHT: [number, number, number, number] = [165, 205, 255, 1];
const GLITCH_RGB = "110, 170, 255";

const mod = (a: number, n: number) => ((a % n) + n) % n;
const smooth = (t: number) => t * t * (3 - 2 * t);

function patternAt(col: number, row: number, tx: number, ty: number): string {
  const r = row + ty; // узор едет вверх
  const tileRow = Math.floor(r / PERIOD_Y);
  const c = col - tx + (mod(tileRow, 2) ? PERIOD_X / 2 : 0); // и вправо
  const y = mod(r, PERIOD_Y);
  const x = mod(c, PERIOD_X);
  if (y >= SPRITE_H || x >= SPRITE_W) return " ";
  return SCALES[y][x];
}

// Цвет по (яркость от курсора, затухание у края) — строки кэшируются.
const colorCache = new Map<number, string>();
function colorFor(light: number, fade: number): string {
  const key = light * 100 + fade;
  let color = colorCache.get(key);
  if (!color) {
    const t = light / LEVELS;
    const f = fade / LEVELS;
    const ch = (i: number) => Math.round(BASE[i] + (BRIGHT[i] - BASE[i]) * t);
    const a = (BASE[3] + (BRIGHT[3] - BASE[3]) * t) * f;
    color = `rgba(${ch(0)}, ${ch(1)}, ${ch(2)}, ${a.toFixed(3)})`;
    colorCache.set(key, color);
  }
  return color;
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
    let light = new Uint8Array(0); // текущая ступень яркости от курсора
    let fade = new Uint8Array(0); // затухание у края, 0..LEVELS — считается при resize
    let noise = new Float32Array(0); // неровность края пятна курсора
    let tx = 0;
    let ty = 0;
    let steps = 0;
    const mouse = { x: -9999, y: -9999 };
    const spot = { x: -9999, y: -9999, power: 0 }; // пятно догоняет мышь

    const drawCell = (i: number, ch: string, color: string) => {
      const x = (i % cols) * cellW;
      const y = Math.floor(i / cols) * ROW_PX;
      ctx.clearRect(x, y, cellW, ROW_PX);
      if (ch !== " " && fade[i] > 0) {
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
      light = new Uint8Array(n);
      fade = new Uint8Array(n);
      noise = new Float32Array(n);
      ctx.clearRect(0, 0, w, h);
      for (let i = 0; i < n; i++) {
        const c = i % cols;
        const r = (i - c) / cols;
        const ex = Math.min(c / cols, 1 - c / cols) / EDGE_FADE;
        const ey = Math.min(r / rows, 1 - r / rows) / EDGE_FADE;
        fade[i] = Math.round(smooth(Math.min(1, ex)) * smooth(Math.min(1, ey)) * LEVELS);
        noise[i] = (Math.random() - 0.5) * 0.35;
        target[i] = patternAt(c, r, tx, ty);
        shown[i] = target[i];
        drawCell(i, shown[i], colorFor(0, fade[i]));
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
          delay[i] = Math.floor(Math.random() * SPREAD_FRAMES);
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

      // Пятно плавно догоняет курсор, проявляется и гаснет тоже плавно.
      const present = mouse.x > -9000;
      if (present) {
        if (spot.x < -9000) { spot.x = mouse.x; spot.y = mouse.y; }
        spot.x += (mouse.x - spot.x) * CURSOR_FOLLOW;
        spot.y += (mouse.y - spot.y) * CURSOR_FOLLOW;
      }
      spot.power += ((present ? 1 : 0) - spot.power) * 0.08;

      const sc = spot.x / cellW;
      const sr = spot.y / ROW_PX;
      const rc = CURSOR_R / cellW;
      const rr = CURSOR_R / ROW_PX;
      for (let i = 0; i < target.length; i++) {
        if (fade[i] === 0) continue;
        const c = i % cols;
        const r = (i - c) / cols;
        const dx = (c - sc) / rc;
        const dy = (r - sr) / rr;
        // мягкий спад к краю пятна + шум, чтобы край не был ровным кругом
        const d = Math.sqrt(dx * dx + dy * dy) + noise[i];
        const k = d < 1 ? smooth(1 - Math.max(0, d)) * spot.power : 0;
        const lvl = Math.round(k * LEVELS);
        const hasGlyph = target[i] !== " " || shown[i] !== " ";
        // ближе к центру пятна символы изредка сбоят сами по себе
        if (k > 0.15 && target[i] !== " " && scramble[i] === 0 && Math.random() < 0.035 * k) {
          scramble[i] = 1 + Math.floor(Math.random() * 2);
        }
        if (delay[i] > 0) { delay[i] -= 1; continue; }
        if (scramble[i] > 0) {
          scramble[i] -= 1;
          const settled = scramble[i] === 0;
          const ch = settled ? target[i] : GLITCH[(Math.random() * GLITCH.length) | 0];
          shown[i] = ch;
          light[i] = lvl;
          drawCell(i, ch, settled
            ? colorFor(lvl, fade[i])
            : `rgba(${GLITCH_RGB}, ${(0.85 * fade[i] / LEVELS).toFixed(3)})`);
        } else if (hasGlyph && (shown[i] !== target[i] || light[i] !== lvl)) {
          shown[i] = target[i];
          light[i] = lvl;
          drawCell(i, shown[i], colorFor(lvl, fade[i]));
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
