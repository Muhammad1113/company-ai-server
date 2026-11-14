import { useEffect, useRef } from 'react';

// Interactive glass sphere for the dashboard (no library, plain canvas).
//  - the light and the colours inside follow the mouse anywhere on the page
//  - hover makes it swell a little, click makes it pulse with a ripple
//  - drag it sideways and it spins, then slowly settles
// Colours change with day / night mode. prefers-reduced-motion => one still frame.

const PAL = {
  dark: {
    glow: [70, 190, 220],
    base: ['rgb(28,120,150)', 'rgb(10,66,112)', 'rgb(3,20,44)'],
    blobs: [
      { c: [70, 235, 170], r: 0.62, ax: 0.42, ay: 0.3, sx: 0.5, sy: 0.38, ph: 0.2, a: 0.85 },
      { c: [60, 130, 255], r: 0.75, ax: 0.32, ay: 0.42, sx: 0.36, sy: 0.52, ph: 2.1, a: 0.8 },
      { c: [255, 214, 92], r: 0.46, ax: 0.5, ay: 0.36, sx: 0.44, sy: 0.3, ph: 4.0, a: 0.8 },
      { c: [120, 240, 255], r: 0.5, ax: 0.38, ay: 0.5, sx: 0.6, sy: 0.46, ph: 5.2, a: 0.6 },
    ],
    shade: 'rgba(0,6,24,0.55)',
    rim: [170, 240, 255],
    mode: 'lighter',
  },
  light: {
    glow: [160, 130, 255],
    base: ['rgb(240,233,255)', 'rgb(205,188,255)', 'rgb(150,124,238)'],
    blobs: [
      { c: [255, 255, 255], r: 0.6, ax: 0.4, ay: 0.3, sx: 0.46, sy: 0.38, ph: 0.4, a: 0.9 },
      { c: [190, 160, 255], r: 0.7, ax: 0.34, ay: 0.4, sx: 0.34, sy: 0.5, ph: 2.4, a: 0.75 },
      { c: [255, 190, 235], r: 0.45, ax: 0.5, ay: 0.34, sx: 0.42, sy: 0.3, ph: 3.9, a: 0.6 },
      { c: [160, 200, 255], r: 0.5, ax: 0.36, ay: 0.48, sx: 0.58, sy: 0.44, ph: 5.1, a: 0.6 },
    ],
    shade: 'rgba(96,60,190,0.30)',
    rim: [255, 255, 255],
    mode: 'source-over',
  },
};

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export default function Orb({ size = 210 }) {
  const ref = useRef(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return undefined;

    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    let S = size; let dpr = 1; let raf = 0;
    const st = {
      tx: 0, ty: 0, x: 0, y: 0,          // pointer target / smoothed (-1..1)
      hover: false, down: false, lastX: 0,
      scale: 1, vel: 0, spin: 0, spinV: 0.0,
      ripples: [],
    };
    const t0 = performance.now();

    function resize() {
      const box = canvas.getBoundingClientRect();
      S = Math.max(60, Math.round(box.width || size));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(S * dpr);
      canvas.height = Math.round(S * dpr);
    }

    function draw(t) {
      const theme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
      const P = PAL[theme];
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, S, S);

      const R = S * 0.27 * st.scale;
      const cx = S / 2 + st.x * S * 0.025;
      const cy = S / 2 + st.y * S * 0.025 + Math.sin(t * 0.9) * S * 0.008;
      const [gr, gg, gb] = P.glow;

      // soft outer glow
      let g = ctx.createRadialGradient(cx, cy, R * 0.55, cx, cy, R * 1.78);
      g.addColorStop(0, `rgba(${gr},${gg},${gb},0.42)`);
      g.addColorStop(0.5, `rgba(${gr},${gg},${gb},0.12)`);
      g.addColorStop(1, `rgba(${gr},${gg},${gb},0)`);
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.78, 0, Math.PI * 2); ctx.fill();

      // click ripples
      for (const rp of st.ripples) {
        const k = (t - rp.t0) / 1.1;
        if (k < 0 || k > 1) continue;
        ctx.strokeStyle = `rgba(${gr},${gg},${gb},${(1 - k) * 0.55})`;
        ctx.lineWidth = 2 * (1 - k) + 0.5;
        ctx.beginPath(); ctx.arc(cx, cy, R * (1.02 + k * 0.62), 0, Math.PI * 2); ctx.stroke();
      }

      // light position follows the pointer
      const lx = cx + st.x * R * 0.5 - R * 0.28;
      const ly = cy + st.y * R * 0.5 - R * 0.32;

      ctx.save();
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.clip();

      // body
      g = ctx.createRadialGradient(lx, ly, R * 0.04, cx, cy, R * 1.08);
      g.addColorStop(0, P.base[0]);
      g.addColorStop(0.55, P.base[1]);
      g.addColorStop(1, P.base[2]);
      ctx.fillStyle = g;
      ctx.fillRect(cx - R, cy - R, R * 2, R * 2);

      // drifting colour blobs (parallax with the pointer gives depth)
      ctx.globalCompositeOperation = P.mode;
      for (const b of P.blobs) {
        const a1 = t * b.sx + b.ph + st.spin;
        const a2 = t * b.sy + b.ph * 1.7 - st.spin * 0.6;
        const bx = cx + Math.cos(a1) * R * b.ax + st.x * R * 0.22;
        const by = cy + Math.sin(a2) * R * b.ay + st.y * R * 0.22;
        const br = R * b.r;
        const [r, gC, bC] = b.c;
        const bg = ctx.createRadialGradient(bx, by, 0, bx, by, br);
        bg.addColorStop(0, `rgba(${r},${gC},${bC},${b.a})`);
        bg.addColorStop(1, `rgba(${r},${gC},${bC},0)`);
        ctx.fillStyle = bg;
        ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
      }
      ctx.globalCompositeOperation = 'source-over';

      // far side falls into shade
      g = ctx.createRadialGradient(lx, ly, R * 0.35, cx, cy, R * 1.05);
      g.addColorStop(0, 'rgba(0,0,0,0)');
      g.addColorStop(1, P.shade);
      ctx.fillStyle = g;
      ctx.fillRect(cx - R, cy - R, R * 2, R * 2);

      // glass rim (fresnel)
      const [rr, rg, rb] = P.rim;
      g = ctx.createRadialGradient(cx, cy, R * 0.74, cx, cy, R);
      g.addColorStop(0, `rgba(${rr},${rg},${rb},0)`);
      g.addColorStop(1, `rgba(${rr},${rg},${rb},0.5)`);
      ctx.fillStyle = g;
      ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
      ctx.restore();

      // thin edge
      ctx.strokeStyle = `rgba(${rr},${rg},${rb},0.5)`;
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.arc(cx, cy, R - 0.6, 0, Math.PI * 2); ctx.stroke();

      // specular highlight
      const hx = cx + (lx - cx) * 0.78;
      const hy = cy + (ly - cy) * 0.78;
      g = ctx.createRadialGradient(hx, hy, 0, hx, hy, R * 0.34);
      g.addColorStop(0, 'rgba(255,255,255,0.95)');
      g.addColorStop(0.35, 'rgba(255,255,255,0.35)');
      g.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.ellipse(hx, hy, R * 0.34, R * 0.26, -0.6, 0, Math.PI * 2); ctx.fill();

      // soft bounce light on the opposite edge
      const bx2 = cx - (lx - cx) * 0.8;
      const by2 = cy - (ly - cy) * 0.8;
      g = ctx.createRadialGradient(bx2, by2, 0, bx2, by2, R * 0.5);
      g.addColorStop(0, `rgba(${gr},${gg},${gb},0.28)`);
      g.addColorStop(1, `rgba(${gr},${gg},${gb},0)`);
      ctx.save();
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.clip();
      ctx.fillStyle = g;
      ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
      ctx.restore();
    }

    function inside(e) {
      const r = canvas.getBoundingClientRect();
      const dx = e.clientX - (r.left + r.width / 2);
      const dy = e.clientY - (r.top + r.height / 2);
      return Math.hypot(dx, dy) < r.width * 0.34;
    }
    function onMove(e) {
      st.tx = clamp((e.clientX / Math.max(1, window.innerWidth)) * 2 - 1, -1, 1);
      st.ty = clamp((e.clientY / Math.max(1, window.innerHeight)) * 2 - 1, -1, 1);
      st.hover = inside(e);
      if (st.down) { st.spinV += (e.clientX - st.lastX) * 0.004; st.lastX = e.clientX; }
    }
    function onDown(e) {
      if (!inside(e)) return;
      st.down = true; st.lastX = e.clientX;
      st.vel += 0.06;
      st.ripples.push({ t0: (performance.now() - t0) / 1000 });
      if (st.ripples.length > 4) st.ripples.shift();
    }
    function onUp() { st.down = false; }
    function onLeave() { st.tx = 0; st.ty = 0; st.hover = false; st.down = false; }

    resize();
    draw(3);
    const ro = new ResizeObserver(() => { resize(); draw((performance.now() - t0) / 1000); });
    ro.observe(canvas);

    if (!reduce) {
      let last = performance.now();
      const frame = (now) => {
        raf = requestAnimationFrame(frame);
        const dt = Math.min(0.05, (now - last) / 1000);
        last = now;
        const k = dt * 60;
        st.x += (st.tx - st.x) * Math.min(1, 0.07 * k);
        st.y += (st.ty - st.y) * Math.min(1, 0.07 * k);
        // spring for hover / click pulse
        const target = st.hover ? 1.06 : 1;
        st.vel += (target - st.scale) * 0.12 * k;
        st.vel *= Math.pow(0.84, k);
        st.scale += st.vel * k;
        // spin from dragging, then settle
        st.spin += st.spinV * k;
        st.spinV *= Math.pow(0.95, k);
        st.spin += 0.0016 * k;
        draw((now - t0) / 1000);
      };
      raf = requestAnimationFrame(frame);
      window.addEventListener('pointermove', onMove, { passive: true });
      window.addEventListener('pointerdown', onDown, { passive: true });
      window.addEventListener('pointerup', onUp);
      document.addEventListener('pointerleave', onLeave);
    }
    return () => {
      cancelAnimationFrame(raf); ro.disconnect();
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerdown', onDown);
      window.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointerleave', onLeave);
    };
  }, [size]);

  return <canvas ref={ref} aria-hidden="true" />;
}
