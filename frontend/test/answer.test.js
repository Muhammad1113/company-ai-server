import test from 'node:test';
import assert from 'node:assert/strict';
import { answerOrigin, cleanAnswer, dropGeneralLabel, highlightRanges, citingText } from '../src/lib/answer.js';
import { planSources } from '../src/lib/sources.js';

const docs = [
  { ref: 1, kind: 'doc', doc_id: 'ecfr-21cfr807', page: 3, preview: 'Establishment registration and device listing must be submitted before import. The official correspondent handles FDA communication.' },
  { ref: 2, kind: 'doc', doc_id: 'openfda-gei-recall', page: 1, preview: 'recall status: Open, Classified' },
];

test('retrieved but uncited sources => general, not company documents', () => {
  const t = 'From general knowledge: FDA labeling covers name, intended use and warnings for every device sold.';
  assert.equal(answerOrigin(t, docs), 'general');
  assert.equal(planSources(docs, t, false).hidden, true);
});

test('fully cited => docs', () => {
  const t = 'Foreign manufacturers must register the establishment and list devices before import into the USA [1].';
  assert.equal(answerOrigin(t, docs), 'docs');
});

test('cited + uncited claims => docgen', () => {
  const t = 'Registration and listing must be submitted before import into the USA [1].\n\nGeneral knowledge: A 510(k) shows the device is substantially equivalent to a predicate device.';
  assert.equal(answerOrigin(t, docs), 'docgen');
});

test('greeting without sources has no badge', () => {
  assert.equal(answerOrigin('Hello! How can I help you with export planning today?', null), null);
});

test('meta notes are removed from display', () => {
  const t = 'Answer line here [1].\n\nNote: This answer is based on general knowledge as the provided company documents only contain recall data.\nCheck numbers and certificates against the source before sending to a buyer.';
  assert.equal(cleanAnswer(t), 'Answer line here [1].');
});

test('highlight picks the sentence matching the claim', () => {
  const claim = citingText('Registration and listing must be submitted before import [1].', 1);
  const r = highlightRanges(docs[0].preview, claim);
  assert.equal(r.length, 1);
  assert.match(docs[0].preview.slice(...r[0]), /Establishment registration/);
});

test('general answer: prefix and contradictory "not in documents" line are removed', () => {
  const raw = 'General knowledge: AISI 420 hardens more than 410, so it suits cutting edges.\nThis is not in the company documents.';
  const cleaned = cleanAnswer(raw);
  assert.equal(answerOrigin(cleaned, docs), 'general');
  assert.equal(dropGeneralLabel(cleaned), 'AISI 420 hardens more than 410, so it suits cutting edges.');
});

test('a lone "not in the company documents" answer is kept', () => {
  assert.equal(cleanAnswer('This is not in the company documents.'), 'This is not in the company documents.');
});

test('inline "General knowledge:" labels are removed everywhere', () => {
  assert.equal(dropGeneralLabel('Wa alaikum assalam. General knowledge: Verify your product codes.'), 'Wa alaikum assalam. Verify your product codes.');
  assert.equal(dropGeneralLabel('Registration is required [1].\n\nGeneral knowledge: 510(k) shows equivalence.'), 'Registration is required [1].\n\n510(k) shows equivalence.');
});
