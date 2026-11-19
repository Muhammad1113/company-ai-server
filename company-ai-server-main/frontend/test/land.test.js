import test from 'node:test';
import assert from 'node:assert/strict';
import { isLand, landPoints, inPolygon } from '../src/lib/land.js';

test('inPolygon: simple square', () => {
  const sq = [[0, 0], [10, 0], [10, 10], [0, 10]];
  assert.equal(inPolygon(5, 5, sq), true);
  assert.equal(inPolygon(15, 5, sq), false);
});

test('mashhoor jagahen zameen hain', () => {
  const land = { London: [-0.1, 51.5], Sahara: [15, 25], Delhi: [77, 28.6], Karachi: [67, 25], Amazon: [-60, -5], Texas: [-99, 31], Sydney: [147, -33], Moscow: [37.6, 55.7], Beijing: [116, 40] };
  for (const [name, [lon, lat]] of Object.entries(land)) assert.equal(isLand(lon, lat), true, name);
});

test('samundar aur khaliyan paani hain', () => {
  const sea = { midAtlantic: [-40, 30], pacific: [-150, 0], indian: [75, -20], arabianSea: [65, 15], blackSea: [34, 43.5], hudson: [-85, 60], southPacific: [-120, -40] };
  for (const [name, [lon, lat]] of Object.entries(sea)) assert.equal(isLand(lon, lat), false, name);
});

test('landPoints: sahi tadaad, hadood mein, har baar wahi (seeded)', () => {
  const a = landPoints();
  const b = landPoints();
  assert.equal(a.length % 4, 0);
  const n = a.length / 4;
  assert.ok(n > 15000 && n < 45000, `dots = ${n}`);
  assert.deepEqual(Array.from(a.slice(0, 40)), Array.from(b.slice(0, 40)));
  for (let i = 0; i < a.length; i += 4) {
    assert.ok(Math.abs(a[i]) <= Math.PI / 2 + 1e-6);
    assert.ok(a[i + 2] > 0 && a[i + 2] <= 1);
  }
});
