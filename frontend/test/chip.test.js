import test from 'node:test';
import assert from 'node:assert/strict';
import { sourceChip } from '../src/lib/sources.js';

test('FDA/eCFR passage section dikhata hai, Part N nahi', () => {
  assert.equal(sourceChip({ section: '21 CFR 820.30', chunk_index: 4, page: null }), '21 CFR 820.30');
});
test('PDF page ho to Page N', () => {
  assert.equal(sourceChip({ page: 3, chunk_index: 0 }), 'Page 3');
});
test('purane sources (section nahi) ka label wahi rehta hai: Part N', () => {
  assert.equal(sourceChip({ page: null, chunk_index: 0 }), 'Part 1');
  assert.equal(sourceChip({ chunk_index: '2' }), 'Part 3');
  assert.equal(sourceChip(null), '');
});
