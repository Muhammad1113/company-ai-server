import { useEffect, useRef } from 'react';
import { landPoints } from '../lib/land.js';

// Ghoomta hua dotted Earth + orbits + planets. Sirf canvas; koi library nahi.
// Mouse hilane par Earth halka sa follow karta hai (parallax); box mein band nahi, poori screen par.
const TILT = 0.42;            // north pole viewer ki taraf jhuka hua
const SPIN = 0.055;           // rad/sec (~114 sec mein ek chakkar)

let cachedPoints = null;
const getPoints = () => (cachedPoints ||= landPoints());

function makeSprite(size = 32) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  const r = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  r.addColorStop(0, 'rgba(255,255,255,1)');
  r.addColorStop(0.22, 'rgba(190,240,255,0.85)');
  r.addColorStop(0.55, 'rgba(90,190,255,0.22)');
  r.addColorStop(1, 'rgba(60,150,255,0)');
  g.fillStyle = r;
  g.fillRect(0, 0, size, size);
  return c;
}

export default function SpaceScene({ animate = true }) {
  const ref = useRef(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return undefined;

    const reduce = !animate || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const pts = getPoints();
    const n = pts.length / 4;
    const sinLat = new Float32Array(n);
    const cosLat = new Float32Array(n);
    for (let i = 0; i < n; i++) { sinLat[i] = Math.sin(pts[i * 4]); cosLat[i] = Math.cos(pts[i * 4]); }
    const sprite = makeSprite();

    let W = 0; let H = 0; let dpr = 1; let raf = 0;
    const ptr = { x: 0, y: 0, sx: 0, sy: 0 };   // mouse (-1..1) aur uska smooth version
    let panelRight = 0;
    let rootEl = null;
    let stride = 1;                // dheema PC par aadhe dots
    let minInterval = 0;           // phir bhi dheema ho to frame rate kam
    const t0 = performance.now();

    function resize() {
      const box = canvas.parentElement.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      W = Math.max(1, Math.round(box.width));
      H = Math.max(1, Math.round(box.height));
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      rootEl = canvas.closest('.auth');
      const panel = rootEl?.querySelector('.auth-panel');
      panelRight = W > 820 && panel ? panel.getBoundingClientRect().right - box.left : 0;
    }

    function onMove(e) {
      ptr.x = (e.clientX / Math.max(1, window.innerWidth)) * 2 - 1;
      ptr.y = (e.clientY / Math.max(1, window.innerHeight)) * 2 - 1;
    }
    function onLeave() { ptr.x = 0; ptr.y = 0; }

    function draw(t) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      // Earth panel ke baad bachi hui jagah mein beech mein; mobile par upar
      const wide = panelRight > 0;
      const free = wide ? W - panelRight : W;
      // Bada Earth, frame ke daayen kinare se kata hua (design ki tarah): baayen kinara panel se thori door
      const R = wide ? Math.min(H * 0.5, free * 0.8) : Math.min(W * 0.44, H * 0.25);
      const baseX = wide ? panelRight + free * 0.17 + R : W * 0.5;
      const baseY = wide ? H * 0.52 : H * 0.23;
      const cx = baseX + ptr.sx * R * 0.05;
      const cy = baseY + ptr.sy * R * 0.035;
      const sinT = Math.sin(TILT + ptr.sy * 0.16);
      const cosT = Math.cos(TILT + ptr.sy * 0.16);

      const planet = (px, py, pr) => {
        const g = ctx.createRadialGradient(px - pr * 0.4, py - pr * 0.4, pr * 0.1, px, py, pr);
        g.addColorStop(0, 'rgb(74,92,138)');
        g.addColorStop(1, 'rgb(7,11,26)');
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(px, py, pr, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = 'rgba(150,200,255,0.4)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(px, py, pr, Math.PI * 0.85, Math.PI * 1.45); ctx.stroke();
      };

      // --- orbit lines (globe ke peeche, panel ke neeche tak jati hain) + upar baayen chhota planet
      ctx.save();
      ctx.translate(baseX - R * 0.2 - ptr.sx * R * 0.08, baseY + R * 0.04 - ptr.sy * R * 0.05);
      ctx.rotate(-0.34 + Math.sin(t * 0.05) * 0.03);
      ctx.strokeStyle = 'rgba(218,196,150,0.17)';
      ctx.lineWidth = 1;
      const orbits = [[R * 1.5, R * 0.72], [R * 1.28, R * 1.02], [R * 1.95, R * 0.5], [R * 1.72, R * 1.36], [R * 2.3, R * 0.95]];
      for (const [a, b] of orbits) { ctx.beginPath(); ctx.ellipse(0, 0, a, b, 0, 0, Math.PI * 2); ctx.stroke(); }
      ctx.restore();
      planet(baseX - R * 0.84 + Math.sin(t * 0.12) * R * 0.015, baseY - R * 0.64 + Math.cos(t * 0.1) * R * 0.012, R * 0.085);

      // --- atmosphere
      const atm = ctx.createRadialGradient(cx, cy, R * 0.97, cx, cy, R * 1.16);
      atm.addColorStop(0, 'rgba(100,200,255,0.32)');
      atm.addColorStop(1, 'rgba(100,200,255,0)');
      ctx.fillStyle = atm;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.16, 0, Math.PI * 2); ctx.fill();

      // --- globe body
      const body = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.15, R * 0.05, cx, cy, R);
      body.addColorStop(0, 'rgb(20,46,92)');
      body.addColorStop(0.6, 'rgb(6,14,34)');
      body.addColorStop(1, 'rgb(2,5,15)');
      ctx.fillStyle = body;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();

      // --- city lights (zameen ke dots), globe ghoom raha hai
      const rot = t * SPIN + ptr.sx * 0.45;
      ctx.save();
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.clip();
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = '#d2f0ff';
      for (let i = 0; i < n; i += stride) {
        const j = i * 4;
        const lon = pts[j + 1] + rot;
        const cl = cosLat[i];
        const x3 = cl * Math.sin(lon);
        const z0 = cl * Math.cos(lon);
        const y3 = sinLat[i];
        const y = y3 * cosT - z0 * sinT;
        const z = y3 * sinT + z0 * cosT;
        if (z < 0.03) continue;
        const w = pts[j + 2];
        const tw = 0.78 + 0.22 * Math.sin(t * (1.1 + w * 2.2) + pts[j + 3]);
        const a = Math.min(1, (0.2 + w * 1.1) * tw * (0.3 + 0.7 * Math.pow(z, 0.55)));
        const s = (0.5 + w * 1.25) * (0.55 + 0.45 * z);
        const sx = cx + x3 * R;
        const sy = cy - y * R;
        ctx.globalAlpha = a;
        if (w > 0.66) {
          const gs = s * 4.6;
          ctx.drawImage(sprite, sx - gs / 2, sy - gs / 2, gs, gs);
        } else {
          ctx.fillRect(sx - s / 2, sy - s / 2, s, s);
        }
      }
      ctx.restore();

      // --- 3D shading (daayen/neeche andhera) + rim light (baayen kinara)
      const shade = ctx.createRadialGradient(cx - R * 0.55, cy - R * 0.1, R * 0.4, cx, cy, R * 1.02);
      shade.addColorStop(0, 'rgba(0,0,10,0)');
      shade.addColorStop(1, 'rgba(0,0,10,0.62)');
      ctx.fillStyle = shade;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();

      const rim = ctx.createLinearGradient(cx - R, cy + R * 0.45, cx + R * 0.25, cy - R);
      rim.addColorStop(0, 'rgba(205,248,255,1)');
      rim.addColorStop(0.45, 'rgba(120,205,255,0.55)');
      rim.addColorStop(1, 'rgba(120,205,255,0)');
      ctx.save();
      ctx.shadowColor = 'rgba(120,210,255,0.9)';
      ctx.shadowBlur = 22;
      ctx.strokeStyle = rim;
      ctx.lineWidth = 2.4;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();

      // chhota planet globe ke aage (neeche daayen)
      planet(cx + R * 0.7 + Math.sin(t * 0.09) * R * 0.012, cy + R * 0.62, R * 0.065);
    }

    resize();
    draw(reduce ? 4 : 0);
    const ro = new ResizeObserver(() => { resize(); draw(reduce ? 4 : (performance.now() - t0) / 1000); });
    ro.observe(canvas.parentElement);

    if (!reduce) {
      let last = performance.now();
      let ema = 16;
      let counted = 0;
      let level = 0;
      const frame = (now) => {
        raf = requestAnimationFrame(frame);
        const dt = now - last;
        if (dt < minInterval) return;
        last = now;
        ptr.sx += (ptr.x - ptr.sx) * 0.07;
        ptr.sy += (ptr.y - ptr.sy) * 0.07;
        rootEl?.style.setProperty('--px', ptr.sx.toFixed(3));
        rootEl?.style.setProperty('--py', ptr.sy.toFixed(3));
        draw((now - t0) / 1000);
        // Quality khud adjust: frame lamba ho to dots kam, phir fps kam
        ema = ema * 0.9 + Math.min(dt, 200) * 0.1;
        if (++counted >= 45 && ema > 45 && level < 2) {
          level += 1;
          if (level === 1) stride = 2; else minInterval = 80;
          counted = 0;
          ema = 16;
        }
      };
      raf = requestAnimationFrame(frame);
      window.addEventListener('pointermove', onMove, { passive: true });
      document.addEventListener('pointerleave', onLeave);
    }
    return () => {
      cancelAnimationFrame(raf); ro.disconnect();
      window.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerleave', onLeave);
    };
  }, [animate]);

  return <canvas ref={ref} className="scene" aria-hidden="true" />;
}
